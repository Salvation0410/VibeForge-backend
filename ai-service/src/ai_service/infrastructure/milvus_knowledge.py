from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import struct
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, AsyncContextManager, Literal, Protocol

from pymilvus import MilvusClient

from ai_service.config import Settings

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CHUNK_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,512}")
_MILVUS_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,254}")
_MAX_MILVUS_IDENTIFIER_LENGTH = 255
_COLLECTION_BASE_LENGTH = _MAX_MILVUS_IDENTIFIER_LENGTH - max(
    len("_000000000000_staging_000000000000"),
    len("_mutation_control_v1_000000000000"),
)
_VERIFY_BATCH_SIZE = 256
_REBUILD_INSERT_BATCH_SIZE = 100
_MAX_DOCUMENT_HISTORY_RECORDS = 10_000
_OUTPUT_FIELDS = [
    "chunkId", "documentId", "documentVersion", "chunkIndex", "etlVersion",
    "embeddingModelVersion", "fileName", "fileType", "sourceLocator", "content",
    "contentHash", "embeddingDimension", "schemaVersion", "isActive",
]
_VERIFIED_FIELDS = {
    "id", "recordType", "chunkId", "documentId", "documentVersion", "chunkIndex",
    "etlVersion", "embeddingModelVersion", "fileName", "fileType", "sourceLocator",
    "content", "contentHash", "documentContentHash", "embeddingDimension",
    "schemaVersion", "mutationFence", "isActive", "embedding",
    "rebuildFingerprint", "actualDocumentCount", "actualChunkCount",
}


class RebuildFingerprint:
    """Order-sensitive, incremental fingerprint shared by planning and storage."""

    def __init__(self, collection_alias: str, etl_version: str) -> None:
        self._hasher = hashlib.sha256()
        self._add("customer-service-rebuild-v1")
        self._add(collection_alias)
        self._add(etl_version)

    def _add(self, value: object) -> None:
        encoded = str(value).encode("utf-8")
        self._hasher.update(len(encoded).to_bytes(8, "big"))
        self._hasher.update(encoded)

    def add_document(
        self, *, document_id: str, document_version: int, file_name: str,
        file_type: str, content_hash: str,
    ) -> None:
        for value in (
            document_id, document_version, file_name, file_type.lower(), content_hash,
        ):
            self._add(value)

    def hexdigest(self) -> str:
        return self._hasher.hexdigest()


class MilvusKnowledgeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class KnowledgeMutationLease:
    scope: str
    operation_id: str
    operation: Literal["INDEX", "DELETE", "REBUILD"]
    fence: int
    expires_at: float
    proof: str


class MutationPermit(Protocol):
    """A held, externally fenced mutation permit."""

    fence: int

    async def assert_current(self) -> None: ...


class KnowledgeMutationCoordinator(Protocol):
    """Trusted cross-instance mutation coordinator.

    Implementations must validate the lease proof and fencing token, never overlap permits
    for the same document, and make a collection scope conflict with every document scope.
    A permit must remain exclusive until its context exits; `assert_current` fails closed if
    the upstream lease is revoked. The store intentionally provides no local-lock fallback.
    """

    def hold(
        self, lease: KnowledgeMutationLease, *, scope: str, operation: str
    ) -> AsyncContextManager[MutationPermit]: ...


class DenyAllKnowledgeMutationCoordinator:
    """Default coordinator: mutation is impossible until a trusted coordinator is injected."""

    @asynccontextmanager
    async def hold(
        self, lease: KnowledgeMutationLease, *, scope: str, operation: str
    ) -> AsyncIterator[MutationPermit]:
        raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_REQUIRED")
        yield  # pragma: no cover


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    chunk_id: str
    chunk_index: int
    content: str
    source_locator: str
    content_hash: str
    embedding: tuple[float, ...] | list[float]


@dataclass(frozen=True, slots=True)
class IndexedDocument:
    document_id: str
    document_version: int
    etl_version: str
    embedding_model_version: str
    file_name: str
    file_type: str
    content_hash: str
    chunks: tuple[IndexedChunk, ...] | list[IndexedChunk]


@dataclass(frozen=True, slots=True)
class IndexResult:
    document_id: str
    document_version: int
    chunk_count: int
    collection_name: str
    idempotent: bool = False


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_version: int
    chunk_index: int
    content: str
    file_name: str
    file_type: str
    source_locator: str
    content_hash: str
    distance: float
    score: float
    is_active: bool = True
    current_document_version: int | None = None


@dataclass(frozen=True, slots=True)
class RebuildResult:
    collection_name: str
    document_count: int
    chunk_count: int
    idempotent: bool = False


@dataclass(frozen=True, slots=True)
class RebuildPlan:
    embedding_model_version: str
    embedding_dimension: int
    etl_version: str
    document_count: int
    document_fingerprint: str


class KnowledgeStore(Protocol):
    async def upsert_document_version(
        self, document: IndexedDocument, *, lease: KnowledgeMutationLease | None = None
    ) -> IndexResult: ...
    async def delete_document(
        self, document_id: str, document_version: int, *,
        lease: KnowledgeMutationLease | None = None,
    ) -> None: ...
    async def search(self, vector: list[float], limit: int) -> list[RetrievedChunk]: ...
    async def rebuild_collection(
        self, documents: AsyncIterator[IndexedDocument], *,
        lease: KnowledgeMutationLease | None = None,
        plan: RebuildPlan,
    ) -> RebuildResult: ...
    async def ping(self) -> bool: ...


class MilvusKnowledgeStore:
    """Version-aware Milvus adapter with verified staging and monotonic activation."""

    def __init__(
        self, settings: Settings, *, client_factory: Callable[..., Any] = MilvusClient,
        schema_version: int = SCHEMA_VERSION,
        mutation_coordinator: KnowledgeMutationCoordinator | None = None,
    ) -> None:
        self._alias = settings.milvus_collection_alias
        if not self._valid_collection_identifier(self._alias):
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_ALIAS_INVALID")
        self._schema_version = schema_version
        self._rpc_timeout = settings.milvus_rpc_timeout_seconds
        self._retention_generations = settings.rag_collection_retention_generations
        self._cleanup_grace_seconds = settings.rag_collection_cleanup_grace_seconds
        self._cleanup_timeout_seconds = settings.rag_collection_cleanup_timeout_seconds
        self._cleanup_scan_limit = settings.rag_collection_cleanup_scan_limit
        try:
            self._client = client_factory(
                uri=settings.milvus_uri, token=settings.milvus_token,
                db_name=settings.milvus_database, timeout=self._rpc_timeout,
            )
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        self._mutation_coordinator = (
            mutation_coordinator or DenyAllKnowledgeMutationCoordinator()
        )
        # Local serialization is an optimization; persisted manifests provide cross-worker safety.
        self._write_lock = asyncio.Lock()

    @asynccontextmanager
    async def _mutation(
        self, lease: KnowledgeMutationLease | None, *, scope: str, operation: str,
    ) -> AsyncIterator[MutationPermit]:
        if lease is None:
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_REQUIRED")
        expected_operation = {
            "upsert": "INDEX",
            "delete": "DELETE",
            "rebuild": "REBUILD",
        }.get(operation)
        if (
            lease.scope != scope or not lease.operation_id
            or lease.operation != expected_operation
            or not isinstance(lease.fence, int) or lease.fence < 1
            or not lease.proof or lease.expires_at <= time.time()
        ):
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID")
        try:
            async with self._mutation_coordinator.hold(
                lease, scope=scope, operation=operation
            ) as permit:
                if permit.fence != lease.fence:
                    raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID")
                await self._assert_permit(permit)
                yield permit
        except MilvusKnowledgeError:
            raise
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID") from None

    @staticmethod
    async def _assert_permit(permit: MutationPermit) -> None:
        try:
            await permit.assert_current()
        except MilvusKnowledgeError:
            raise
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_MUTATION_LEASE_INVALID") from None

    async def _call(self, method: str, *args: object, **kwargs: object) -> Any:
        kwargs.setdefault("timeout", self._rpc_timeout)
        return await self._thread_call(
            getattr(self._client, method), *args, **kwargs
        )

    @staticmethod
    async def _thread_call(
        function: Callable[..., Any], *args: object, **kwargs: object,
    ) -> Any:
        task = asyncio.create_task(
            asyncio.to_thread(function, *args, **kwargs)
        )
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError as cancelled:
            # The thread cannot be stopped. Keep the mutation permit held until the bounded
            # pymilvus RPC has a definite outcome, then preserve caller cancellation.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if task.done() and not task.cancelled():
                try:
                    task.result()
                except Exception:
                    pass
            raise cancelled

    @staticmethod
    def _valid_id(value: str) -> bool:
        return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None

    @staticmethod
    def _valid_collection_identifier(value: str) -> bool:
        return (
            isinstance(value, str)
            and _MILVUS_IDENTIFIER_PATTERN.fullmatch(value) is not None
        )

    @staticmethod
    def _canonical_vector(values: Sequence[object]) -> list[float]:
        try:
            vector = [
                struct.unpack("!f", struct.pack("!f", float(value)))[0]
                for value in values
            ]
        except (TypeError, ValueError, OverflowError, struct.error):
            raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_INVALID_VECTOR") from None
        if not vector or not all(math.isfinite(value) for value in vector):
            raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_INVALID_VECTOR")
        return vector

    @classmethod
    def _validate_document(cls, document: IndexedDocument) -> int:
        if (
            not cls._valid_id(document.document_id)
            or not document.etl_version or len(document.etl_version) > 128
            or not document.embedding_model_version or len(document.embedding_model_version) > 255
            or not isinstance(document.document_version, int) or document.document_version < 1
            or not document.file_name or len(document.file_name) > 255
            or not document.file_type or len(document.file_type) > 32
            or not document.content_hash or len(document.content_hash) > 128
            or not document.chunks
        ):
            raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_INVALID")
        dimension: int | None = None
        chunk_ids: set[str] = set()
        indexes: set[int] = set()
        for chunk in document.chunks:
            if (
                not isinstance(chunk.chunk_id, str)
                or _CHUNK_ID_PATTERN.fullmatch(chunk.chunk_id) is None
                or chunk.chunk_id in chunk_ids or chunk.chunk_index in indexes
                or not isinstance(chunk.chunk_index, int) or chunk.chunk_index < 0
                or not chunk.content or not chunk.source_locator or not chunk.content_hash
                or not isinstance(chunk.embedding, Sequence) or not chunk.embedding
            ):
                raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_INVALID")
            chunk_ids.add(chunk.chunk_id)
            indexes.add(chunk.chunk_index)
            vector = cls._canonical_vector(chunk.embedding)
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
        assert dimension is not None
        return dimension

    def _collection_name(self, model: str, dimension: int, *, suffix: str = "") -> str:
        fingerprint = hashlib.sha256(
            f"customer-service\0{self._alias}\0{model}\0{dimension}"
            f"\0{self._schema_version}".encode()
        ).hexdigest()[:12]
        ending = f"_{fingerprint}{suffix}"
        name = f"{self._alias[:_COLLECTION_BASE_LENGTH]}{ending}"
        if not self._valid_collection_identifier(name):
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_ALIAS_INVALID")
        return name

    def _collection_metadata(
        self, model: str, dimension: int, fence: int, *,
        rebuild: tuple[KnowledgeMutationLease, RebuildPlan] | None = None,
    ) -> str:
        metadata = {
            "kind": "customer-service-knowledge",
            "aliasHash": self._alias_hash(),
            "embeddingModelVersion": model,
            "embeddingDimension": dimension,
            "schemaVersion": self._schema_version,
            "mutationFence": fence,
        }
        if rebuild is not None:
            lease, plan = rebuild
            metadata.update({
                "rebuildOperationId": lease.operation_id,
                "rebuildFingerprint": plan.document_fingerprint,
                "rebuildEtlVersion": plan.etl_version,
                "rebuildDocumentCount": plan.document_count,
            })
        return json.dumps(metadata, sort_keys=True, separators=(",", ":"))

    async def _alias_target(self) -> str | None:
        try:
            response = await self._call("list_aliases")
            aliases = response.get("aliases", []) if isinstance(response, dict) else response
            if not isinstance(aliases, list):
                raise TypeError("invalid alias response")
            if self._alias not in aliases:
                return None
            description = await self._call("describe_alias", self._alias)
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        target = description.get("collection_name") or description.get("collection")
        if not target:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE")
        return str(target)

    @staticmethod
    def _described_dimension(description: dict[str, Any]) -> int | None:
        if isinstance(description.get("dimension"), int):
            return int(description["dimension"])
        for field in description.get("fields", []):
            if field.get("name") == "embedding":
                params = field.get("params", {})
                value = params.get("dim") or field.get("dim")
                return int(value) if value is not None else None
        return None

    async def _describe_collection(self, collection: str) -> dict[str, Any]:
        try:
            return await self._call("describe_collection", collection)
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None

    async def _validate_collection(self, collection: str, model: str, dimension: int) -> None:
        canonical = self._collection_name(model, dimension)
        description = await self._describe_collection(collection)
        described_dimension = self._described_dimension(description)
        if collection != canonical and not collection.startswith(f"{canonical}_staging_"):
            if described_dimension != dimension:
                raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH")
        if described_dimension != dimension:
            raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
        try:
            metadata = json.loads(description.get("description", ""))
        except (TypeError, ValueError):
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH") from None
        if (
            metadata.get("kind") != "customer-service-knowledge"
            or metadata.get("embeddingModelVersion") != model
            or metadata.get("embeddingDimension") != dimension
            or metadata.get("schemaVersion") != self._schema_version
            or not isinstance(metadata.get("mutationFence"), int)
            or metadata["mutationFence"] < 1
        ):
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH")

    async def _ensure_collection(
        self, name: str, model: str, dimension: int, fence: int, *,
        rebuild: tuple[KnowledgeMutationLease, RebuildPlan] | None = None,
    ) -> None:
        try:
            if not await self._call("has_collection", name):
                try:
                    await self._call(
                        "create_collection", name, dimension=dimension, primary_field_name="id",
                        id_type="string", vector_field_name="embedding", metric_type="COSINE",
                        auto_id=False, max_length=64, enable_dynamic_field=True,
                        consistency_level="Strong",
                        description=self._collection_metadata(
                            model, dimension, fence, rebuild=rebuild
                        ),
                    )
                except Exception:
                    if not await self._call("has_collection", name):
                        raise
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        await self._validate_collection(name, model, dimension)

    @staticmethod
    def _metadata(description: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(description.get("description", ""))
        except (TypeError, ValueError):
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH") from None
        if not isinstance(value, dict):
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH")
        return value

    def _rebuild_collection_name(
        self, model: str, dimension: int, lease: KnowledgeMutationLease,
        plan: RebuildPlan,
    ) -> str:
        identity = hashlib.sha256(
            f"rebuild\0{self._alias}\0{model}\0{dimension}\0{self._schema_version}"
            f"\0{lease.operation_id}\0{lease.fence}\0{plan.etl_version}"
            f"\0{plan.document_fingerprint}".encode()
        ).hexdigest()[:12]
        return self._collection_name(
            model, dimension, suffix=f"_staging_{identity}"
        )

    async def _validate_rebuild_collection(
        self, collection: str, model: str, dimension: int,
        lease: KnowledgeMutationLease, plan: RebuildPlan,
    ) -> None:
        await self._validate_collection(collection, model, dimension)
        metadata = self._metadata(await self._describe_collection(collection))
        expected = {
            "mutationFence": lease.fence,
            "rebuildOperationId": lease.operation_id,
            "rebuildFingerprint": plan.document_fingerprint,
            "rebuildEtlVersion": plan.etl_version,
            "rebuildDocumentCount": plan.document_count,
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise MilvusKnowledgeError("KNOWLEDGE_REBUILD_STAGING_CONFLICT")

    async def _cleanup_rebuild_candidate(
        self, collection: str, lease: KnowledgeMutationLease,
        plan: RebuildPlan, model: str, dimension: int, *,
        known_absent_before_create: bool,
    ) -> None:
        try:
            if collection != self._rebuild_collection_name(
                model, dimension, lease, plan
            ):
                return
            if await self._alias_target() == collection:
                return
            if not await self._call("has_collection", collection):
                return
            description = await self._describe_collection(collection)
            raw_metadata = description.get("description", "")
            if not raw_metadata and known_absent_before_create:
                owned = True
            else:
                try:
                    metadata = json.loads(raw_metadata)
                except (TypeError, ValueError):
                    return
                expected = {
                    "kind": "customer-service-knowledge",
                    "aliasHash": self._alias_hash(),
                    "embeddingModelVersion": model,
                    "embeddingDimension": dimension,
                    "schemaVersion": self._schema_version,
                    "mutationFence": lease.fence,
                    "rebuildOperationId": lease.operation_id,
                    "rebuildFingerprint": plan.document_fingerprint,
                    "rebuildEtlVersion": plan.etl_version,
                    "rebuildDocumentCount": plan.document_count,
                }
                owned = isinstance(metadata, dict) and all(
                    metadata.get(key) == value for key, value in expected.items()
                )
            if owned:
                await self._call("drop_collection", collection)
        except (Exception, asyncio.CancelledError):
            # Cleanup is best effort. Any uncertain alias or ownership state preserves staging.
            return

    async def _cleanup_retired_collections(
        self, *, current: str, just_replaced: str | None,
        model: str, dimension: int, permit: MutationPermit,
    ) -> None:
        """Best-effort bounded retention after publication; uncertainty preserves data."""

        fence = permit.fence
        deadline = asyncio.get_running_loop().time() + self._cleanup_timeout_seconds

        async def call(
            method: str, *args: object, **kwargs: object,
        ) -> Any:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            kwargs["timeout"] = min(self._rpc_timeout, remaining)
            return await self._call(
                method, *args, **kwargs,
            )

        async def alias_target() -> str | None:
            response = await call("list_aliases")
            aliases = response.get("aliases", []) if isinstance(response, dict) else response
            if not isinstance(aliases, list):
                raise TypeError
            if self._alias not in aliases:
                return None
            description = await call("describe_alias", self._alias)
            target = description.get("collection_name") or description.get("collection")
            if not target:
                raise TypeError
            return str(target)

        async def ensure_control_collection() -> str:
            name = self._control_collection_name()
            if not await call("has_collection", name):
                description = json.dumps({
                    "kind": "customer-service-knowledge-mutation-control",
                    "aliasHash": self._alias_hash(),
                    "schemaVersion": self._schema_version,
                    "mutationFence": fence,
                }, sort_keys=True, separators=(",", ":"))
                try:
                    await self._assert_permit(permit)
                    await call(
                        "create_collection", name, dimension=1,
                        primary_field_name="id", id_type="string",
                        vector_field_name="embedding", metric_type="COSINE",
                        auto_id=False, max_length=64, enable_dynamic_field=True,
                        consistency_level="Strong", description=description,
                    )
                except Exception:
                    if not await call("has_collection", name):
                        raise
            description = await call("describe_collection", name)
            metadata = self._metadata(description)
            if (
                self._described_dimension(description) != 1
                or metadata.get("kind")
                != "customer-service-knowledge-mutation-control"
                or metadata.get("schemaVersion") != self._schema_version
            ):
                raise TypeError
            return name

        async def control_rows(
            control: str, ids: list[str], output_fields: list[str],
        ) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            for start in range(0, len(ids), _VERIFY_BATCH_SIZE):
                rows.extend(await call(
                    "get", control, ids=ids[start:start + _VERIFY_BATCH_SIZE],
                    output_fields=output_fields, consistency_level="Strong",
                ))
            return rows

        async def upsert_control_row(
            control: str, row: dict[str, Any], verified_fields: tuple[str, ...],
        ) -> None:
            await self._assert_permit(permit)
            result = await call("upsert", control, data=[row])
            if self._write_count(result, "upsert") != 1:
                raise TypeError
            actual = await control_rows(
                control, [str(row["id"])], ["id", *verified_fields],
            )
            if len(actual) != 1 or any(
                actual[0].get(field) != row[field]
                for field in ("id", *verified_fields)
            ):
                raise TypeError

        async def retirement_marker(
            control: str, collection: str, retired_at: float,
        ) -> dict[str, Any]:
            row = {
                "id": self._retirement_id(collection),
                "recordType": "collection_retirement",
                "collectionName": collection,
                "retiredAt": retired_at,
                "aliasHash": self._alias_hash(),
                "mutationFence": fence,
                "schemaVersion": self._schema_version,
                "isActive": False,
                "embedding": [0.0],
            }
            await self._assert_permit(permit)
            result = await call("upsert", control, data=[row])
            if self._write_count(result, "upsert") != 1:
                raise TypeError
            return row

        async def write_legacy_markers(
            control: str, collections: list[str], retired_at: float,
        ) -> None:
            if not collections:
                return
            rows = [{
                "id": self._retirement_id(collection),
                "recordType": "collection_retirement",
                "collectionName": collection,
                "retiredAt": retired_at,
                "aliasHash": self._alias_hash(),
                "mutationFence": fence,
                "schemaVersion": self._schema_version,
                "isActive": False,
                "embedding": [0.0],
            } for collection in collections]
            await self._assert_permit(permit)
            result = await call("upsert", control, data=rows)
            if self._write_count(result, "upsert") != len(rows):
                raise TypeError

        async def query_control_rows(
            control: str, expression: str, limit: int,
            output_fields: list[str],
        ) -> list[dict[str, Any]]:
            if limit <= 0:
                return []
            iterator: Any = None
            try:
                iterator = await call(
                    "query_iterator", control,
                    batch_size=min(_VERIFY_BATCH_SIZE, limit), limit=limit,
                    filter=expression, output_fields=output_fields,
                    consistency_level="Strong",
                )
                rows: list[dict[str, Any]] = []
                while len(rows) < limit:
                    batch = await self._thread_call(iterator.next)
                    if not batch:
                        break
                    rows.extend(batch[:limit - len(rows)])
                return rows
            finally:
                if iterator is not None:
                    try:
                        await self._thread_call(iterator.close)
                    except Exception:
                        pass

        async def marker_batch(
            control: str, cursor: str, limit: int,
        ) -> list[dict[str, Any]]:
            if limit <= 0:
                return []
            base_filter = (
                'recordType == "collection_retirement" and '
                f'aliasHash == "{self._alias_hash()}"'
            )
            fields = [
                "id", "recordType", "collectionName", "retiredAt",
                "aliasHash", "schemaVersion",
            ]
            after_filter = base_filter
            if cursor:
                after_filter += f' and id > "{cursor}"'
            rows = await query_control_rows(
                control, after_filter, limit, fields,
            )
            if cursor and len(rows) < limit:
                wrapped = await query_control_rows(
                    control, base_filter,
                    limit - len(rows), fields,
                )
                seen = {str(row.get("id")) for row in rows}
                rows.extend(
                    row for row in wrapped
                    if str(row.get("id")) not in seen
                    and str(row.get("id", "")) <= cursor
                )
            return rows[:limit]

        try:
            async with asyncio.timeout(self._cleanup_timeout_seconds):
                control = await ensure_control_collection()
                if just_replaced is not None:
                    await retirement_marker(
                        control, just_replaced, time.time(),
                    )
                retention_rows = await control_rows(
                    control, [self._retention_state_id()],
                    [
                        "id", "recordType", "protectedCollections",
                        "retentionComplete", "retentionGenerations",
                    ],
                )
                claimed_protected: list[str] = []
                stored_generations: int | None = None
                stored_complete: bool | None = None
                if (
                    len(retention_rows) == 1
                    and retention_rows[0].get("recordType")
                    == "collection_retention_state"
                    and isinstance(
                        retention_rows[0].get("protectedCollections"), list
                    )
                ):
                    claimed_protected = [
                        name for name in retention_rows[0]["protectedCollections"]
                        if isinstance(name, str)
                    ]
                    if isinstance(
                        retention_rows[0].get("retentionComplete"), bool
                    ):
                        stored_complete = bool(
                            retention_rows[0]["retentionComplete"]
                        )
                    if isinstance(
                        retention_rows[0].get("retentionGenerations"), int
                    ):
                        stored_generations = int(
                            retention_rows[0]["retentionGenerations"]
                        )
                if just_replaced is not None:
                    claimed_protected = [
                        just_replaced,
                        *(name for name in claimed_protected if name != just_replaced),
                    ]

                distinct_claims: list[str] = []
                seen_claims: set[str] = set()
                for name in claimed_protected:
                    if name not in seen_claims:
                        seen_claims.add(name)
                        distinct_claims.append(name)

                protected: list[str] = []
                owned_count = 0
                retention_uncertain = False
                validation_count = 0
                target_count = self._retention_generations - 1
                for name in distinct_claims:
                    if len(protected) >= target_count:
                        break
                    if validation_count >= self._cleanup_scan_limit:
                        retention_uncertain = True
                        break
                    validation_count += 1
                    try:
                        if not await call("has_collection", name):
                            continue
                        description = await call("describe_collection", name)
                        metadata = self._metadata(description)
                    except TimeoutError:
                        raise
                    except Exception:
                        protected.append(name)
                        retention_uncertain = True
                        logger.warning(
                            "customer-service protected collection validation "
                            "is uncertain; cleanup remains disabled"
                        )
                        continue
                    owned = (
                        metadata.get("kind") == "customer-service-knowledge"
                        and metadata.get("aliasHash") == self._alias_hash()
                        and metadata.get("schemaVersion") == self._schema_version
                        and isinstance(
                            metadata.get("embeddingModelVersion"), str
                        )
                        and bool(metadata["embeddingModelVersion"])
                        and isinstance(
                            metadata.get("embeddingDimension"), int
                        )
                        and metadata["embeddingDimension"] > 0
                        and self._described_dimension(description)
                        == metadata["embeddingDimension"]
                        and isinstance(metadata.get("mutationFence"), int)
                        and metadata["mutationFence"] >= 1
                    )
                    protected.append(name)
                    if owned:
                        owned_count += 1
                    else:
                        retention_uncertain = True
                        logger.warning(
                            "customer-service protected collection ownership "
                            "is uncertain; cleanup remains disabled"
                        )
                retention_complete = (
                    not retention_uncertain and owned_count >= target_count
                )
                if (
                    just_replaced is not None
                    or stored_generations != self._retention_generations
                    or protected != claimed_protected
                    or stored_complete != retention_complete
                ):
                    retention_row = {
                        "id": self._retention_state_id(),
                        "recordType": "collection_retention_state",
                        "protectedCollections": protected,
                        "retentionComplete": retention_complete,
                        "retentionGenerations": self._retention_generations,
                        "aliasHash": self._alias_hash(),
                        "mutationFence": fence,
                        "schemaVersion": self._schema_version,
                        "isActive": False,
                        "embedding": [0.0],
                    }
                    await upsert_control_row(
                        control, retention_row,
                        (
                            "recordType", "protectedCollections",
                            "retentionComplete", "retentionGenerations",
                            "aliasHash", "schemaVersion",
                        ),
                    )
                keep = {current, *protected}
                if just_replaced is not None:
                    keep.add(just_replaced)
                cursor_rows = await control_rows(
                    control, [self._cleanup_cursor_id()],
                    ["id", "recordType", "cursorMarkerId"],
                )
                cursor = ""
                if (
                    len(cursor_rows) == 1
                    and cursor_rows[0].get("recordType") == "collection_cleanup_cursor"
                    and isinstance(cursor_rows[0].get("cursorMarkerId"), str)
                ):
                    cursor = str(cursor_rows[0]["cursorMarkerId"])
                marker_rows = await marker_batch(
                    control, cursor,
                    self._cleanup_scan_limit - validation_count,
                )
                cutoff = time.time() - self._cleanup_grace_seconds
                for marker in marker_rows:
                    name = marker.get("collectionName")
                    if (
                        not isinstance(name, str) or name in keep
                        or not isinstance(marker.get("retiredAt"), (int, float))
                        or not math.isfinite(float(marker["retiredAt"]))
                        or marker.get("schemaVersion") != self._schema_version
                    ):
                        continue
                    if not retention_complete:
                        continue
                    if float(marker["retiredAt"]) >= cutoff:
                        continue
                    if not await call("has_collection", name):
                        await self._assert_permit(permit)
                        await call("delete", control, ids=[str(marker["id"])])
                        continue
                    description = await call("describe_collection", name)
                    metadata = self._metadata(description)
                    if (
                        metadata.get("kind") != "customer-service-knowledge"
                        or metadata.get("aliasHash") != self._alias_hash()
                        or metadata.get("schemaVersion") != self._schema_version
                    ):
                        continue
                    if await alias_target() != current:
                        return
                    await self._assert_permit(permit)
                    await call("drop_collection", name)
                    await self._assert_permit(permit)
                    await call(
                        "delete", control, ids=[str(marker["id"])],
                    )
                if marker_rows:
                    cursor_row = {
                        "id": self._cleanup_cursor_id(),
                        "recordType": "collection_cleanup_cursor",
                        "cursorMarkerId": str(marker_rows[-1]["id"]),
                        "aliasHash": self._alias_hash(),
                        "mutationFence": fence,
                        "schemaVersion": self._schema_version,
                        "isActive": False,
                        "embedding": [0.0],
                    }
                    await upsert_control_row(
                        control, cursor_row,
                        ("recordType", "cursorMarkerId", "aliasHash", "schemaVersion"),
                    )

                discovery_budget = (
                    self._cleanup_scan_limit
                    - validation_count
                    - len(marker_rows)
                )
                if discovery_budget <= 0:
                    return
                names = await call("list_collections")
                if not isinstance(names, list):
                    raise TypeError
                candidates = sorted(
                    name for name in names
                    if isinstance(name, str)
                    and name not in keep and name != control
                )
                discovery_rows = await control_rows(
                    control, [self._discovery_cursor_id()],
                    ["id", "recordType", "cursorCollectionName"],
                )
                discovery_cursor = ""
                if (
                    len(discovery_rows) == 1
                    and discovery_rows[0].get("recordType")
                    == "collection_discovery_cursor"
                    and isinstance(
                        discovery_rows[0].get("cursorCollectionName"), str
                    )
                ):
                    discovery_cursor = str(
                        discovery_rows[0]["cursorCollectionName"]
                    )
                start = next(
                    (
                        index for index, name in enumerate(candidates)
                        if name > discovery_cursor
                    ),
                    0,
                )
                discovery_batch = (
                    candidates[start:] + candidates[:start]
                )[:discovery_budget]
                existing_markers = await control_rows(
                    control,
                    [self._retirement_id(name) for name in discovery_batch],
                    ["id", "recordType"],
                )
                existing_ids = {
                    str(row.get("id")) for row in existing_markers
                    if row.get("recordType") == "collection_retirement"
                }
                legacy: list[str] = []
                for name in discovery_batch:
                    if self._retirement_id(name) in existing_ids:
                        continue
                    description = await call("describe_collection", name)
                    try:
                        metadata = self._metadata(description)
                    except MilvusKnowledgeError:
                        continue
                    if (
                        metadata.get("kind") == "customer-service-knowledge"
                        and metadata.get("aliasHash") == self._alias_hash()
                        and metadata.get("schemaVersion") == self._schema_version
                    ):
                        legacy.append(name)
                await write_legacy_markers(control, legacy, time.time())
                if discovery_batch:
                    discovery_row = {
                        "id": self._discovery_cursor_id(),
                        "recordType": "collection_discovery_cursor",
                        "cursorCollectionName": discovery_batch[-1],
                        "aliasHash": self._alias_hash(),
                        "mutationFence": fence,
                        "schemaVersion": self._schema_version,
                        "isActive": False,
                        "embedding": [0.0],
                    }
                    await upsert_control_row(
                        control, discovery_row,
                        (
                            "recordType", "cursorCollectionName",
                            "aliasHash", "schemaVersion",
                        ),
                    )
        except TimeoutError:
            logger.warning("customer-service collection cleanup timed out")
        except Exception:
            logger.warning(
                "customer-service collection cleanup deferred",
                exc_info=False,
            )

    @staticmethod
    def _validate_rebuild_plan(plan: RebuildPlan) -> None:
        if (
            not plan.embedding_model_version
            or not isinstance(plan.embedding_dimension, int)
            or plan.embedding_dimension < 1
            or not plan.etl_version or len(plan.etl_version) > 128
            or not isinstance(plan.document_count, int) or plan.document_count < 0
            or not re.fullmatch(r"[0-9a-f]{64}", plan.document_fingerprint)
        ):
            raise MilvusKnowledgeError("KNOWLEDGE_REBUILD_PLAN_INVALID")

    @staticmethod
    def _identity(document: IndexedDocument) -> str:
        return (
            f"{document.document_id}\0{document.document_version}\0{document.etl_version}"
            f"\0{document.embedding_model_version}"
        )

    def _chunk_rows(
        self, document: IndexedDocument, dimension: int, *, active: bool, fence: int,
        chunks: Sequence[IndexedChunk] | None = None,
    ) -> list[dict[str, Any]]:
        identity = self._identity(document)
        return [{
            "id": hashlib.sha256(f"{identity}\0{chunk.chunk_id}".encode()).hexdigest(),
            "recordType": "chunk", "chunkId": chunk.chunk_id,
            "documentId": document.document_id, "documentVersion": document.document_version,
            "chunkIndex": chunk.chunk_index, "etlVersion": document.etl_version,
            "embeddingModelVersion": document.embedding_model_version,
            "fileName": document.file_name, "fileType": document.file_type,
            "sourceLocator": chunk.source_locator, "content": chunk.content,
            "contentHash": chunk.content_hash, "documentContentHash": document.content_hash,
            "embeddingDimension": dimension, "schemaVersion": self._schema_version,
            "mutationFence": fence, "isActive": active,
            "embedding": self._canonical_vector(chunk.embedding),
        } for chunk in (document.chunks if chunks is None else chunks)]

    def _manifest_row(
        self, document: IndexedDocument, dimension: int, chunks: list[dict[str, Any]],
        *, fence: int,
    ) -> dict[str, Any]:
        chunk_ids = [row["id"] for row in chunks]
        checksum = hashlib.sha256("\0".join(chunk_ids).encode()).hexdigest()
        return {
            "id": hashlib.sha256(f"{self._identity(document)}\0manifest".encode()).hexdigest(),
            "recordType": "manifest", "chunkId": f"manifest:{document.document_id}",
            "documentId": document.document_id, "documentVersion": document.document_version,
            "chunkIndex": -1, "etlVersion": document.etl_version,
            "embeddingModelVersion": document.embedding_model_version,
            "fileName": document.file_name, "fileType": document.file_type,
            "sourceLocator": "manifest", "content": "manifest", "contentHash": checksum,
            "documentContentHash": document.content_hash, "embeddingDimension": dimension,
            "schemaVersion": self._schema_version, "isActive": False,
            "embedding": [0.0] * dimension, "chunkIds": chunk_ids,
            "expectedChunkCount": len(chunk_ids), "mutationFence": fence,
        }

    def _rebuild_completion_row(
        self, model: str, dimension: int, lease: KnowledgeMutationLease,
        plan: RebuildPlan, document_count: int, chunk_count: int,
    ) -> dict[str, Any]:
        identity = (
            f"rebuild-complete\0{self._alias}\0{lease.operation_id}\0{lease.fence}"
            f"\0{plan.document_fingerprint}"
        )
        return {
            "id": hashlib.sha256(identity.encode()).hexdigest(),
            "recordType": "rebuild_complete", "chunkId": "rebuild_complete",
            "documentId": "", "documentVersion": 0, "chunkIndex": -2,
            "etlVersion": plan.etl_version, "embeddingModelVersion": model,
            "fileName": "", "fileType": "", "sourceLocator": "rebuild_complete",
            "content": "rebuild_complete", "contentHash": plan.document_fingerprint,
            "documentContentHash": plan.document_fingerprint,
            "embeddingDimension": dimension, "schemaVersion": self._schema_version,
            "mutationFence": lease.fence, "isActive": False,
            "embedding": [0.0] * dimension,
            "rebuildFingerprint": plan.document_fingerprint,
            "actualDocumentCount": document_count,
            "actualChunkCount": chunk_count,
        }

    async def _validated_rebuild_completion(
        self, collection: str, model: str, dimension: int,
        lease: KnowledgeMutationLease, plan: RebuildPlan,
    ) -> dict[str, Any]:
        expected = self._rebuild_completion_row(
            model, dimension, lease, plan, plan.document_count, 0
        )
        rows = await self._get_rows(collection, [expected["id"]])
        if len(rows) != 1:
            raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
        actual = rows[0]
        chunk_count = actual.get("actualChunkCount")
        expected["actualChunkCount"] = chunk_count
        if (
            not isinstance(chunk_count, int) or chunk_count < 0
            or not self._rows_match(actual, expected)
        ):
            raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
        return actual

    @staticmethod
    def _document_filter(document_id: str) -> str:
        return f'documentId == "{document_id}"'

    def _control_collection_name(self) -> str:
        ending = f"_mutation_control_v1_{self._alias_hash()}"
        name = f"{self._alias[:_COLLECTION_BASE_LENGTH]}{ending}"
        if not self._valid_collection_identifier(name):
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_ALIAS_INVALID")
        return name

    def _alias_hash(self) -> str:
        return hashlib.sha256(self._alias.encode()).hexdigest()[:12]

    @staticmethod
    def _tombstone_id(document_id: str, document_version: int) -> str:
        return hashlib.sha256(
            f"knowledge-tombstone\0{document_id}\0{document_version}".encode()
        ).hexdigest()

    @staticmethod
    def _retirement_id(collection: str) -> str:
        return hashlib.sha256(
            f"knowledge-collection-retirement\0{collection}".encode()
        ).hexdigest()

    def _cleanup_cursor_id(self) -> str:
        return hashlib.sha256(
            f"knowledge-collection-cleanup-cursor\0{self._alias}".encode()
        ).hexdigest()

    def _discovery_cursor_id(self) -> str:
        return hashlib.sha256(
            f"knowledge-collection-discovery-cursor\0{self._alias}".encode()
        ).hexdigest()

    def _retention_state_id(self) -> str:
        return hashlib.sha256(
            f"knowledge-collection-retention-state\0{self._alias}".encode()
        ).hexdigest()

    async def _ensure_control_collection(self, fence: int) -> str:
        name = self._control_collection_name()
        try:
            if not await self._call("has_collection", name):
                description = json.dumps({
                    "kind": "customer-service-knowledge-mutation-control",
                    "aliasHash": self._alias_hash(),
                    "schemaVersion": self._schema_version,
                    "mutationFence": fence,
                }, sort_keys=True, separators=(",", ":"))
                try:
                    await self._call(
                        "create_collection", name, dimension=1, primary_field_name="id",
                        id_type="string", vector_field_name="embedding", metric_type="COSINE",
                        auto_id=False, max_length=64, enable_dynamic_field=True,
                        consistency_level="Strong", description=description,
                    )
                except Exception:
                    if not await self._call("has_collection", name):
                        raise
            description = await self._call("describe_collection", name)
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        try:
            metadata = json.loads(description.get("description", ""))
        except (TypeError, ValueError):
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH") from None
        if (
            self._described_dimension(description) != 1
            or metadata.get("kind") != "customer-service-knowledge-mutation-control"
            or metadata.get("schemaVersion") != self._schema_version
        ):
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH")
        return name

    async def _tombstoned_versions(
        self, document_id: str, versions: set[int]
    ) -> set[int]:
        if not versions:
            return set()
        control = self._control_collection_name()
        try:
            if not await self._call("has_collection", control):
                return set()
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        ids = [self._tombstone_id(document_id, version) for version in sorted(versions)]
        rows = await self._get_rows(control, ids)
        return {
            int(row["documentVersion"])
            for row in rows
            if row.get("recordType") == "tombstone"
            and row.get("documentId") == document_id
        }

    async def _write_tombstone(
        self, document_id: str, document_version: int, fence: int
    ) -> None:
        collection = await self._ensure_control_collection(fence)
        row = {
            "id": self._tombstone_id(document_id, document_version),
            "recordType": "tombstone", "documentId": document_id,
            "documentVersion": document_version, "mutationFence": fence,
            "schemaVersion": self._schema_version, "isActive": False,
            "embedding": [0.0],
        }
        await self._write_exact("upsert", collection, [row])
        actual = await self._get_rows(collection, [row["id"]])
        if len(actual) != 1 or any(actual[0].get(key) != value for key, value in row.items()):
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_WRITE_INCOMPLETE")

    async def _clear_retirement_marker_for_publish(
        self, collection: str, permit: MutationPermit,
    ) -> None:
        """Prove a physical target has no stale retirement state before publication."""

        control = self._control_collection_name()
        marker_id = self._retirement_id(collection)
        try:
            if not await self._call("has_collection", control):
                return
            description = await self._call("describe_collection", control)
            metadata = self._metadata(description)
            if (
                self._described_dimension(description) != 1
                or metadata.get("kind")
                != "customer-service-knowledge-mutation-control"
                or metadata.get("schemaVersion") != self._schema_version
            ):
                raise TypeError
            rows = await self._call(
                "get", control, ids=[marker_id],
                output_fields=["id", "recordType", "collectionName"],
                consistency_level="Strong",
            )
            if not rows:
                return
            if len(rows) != 1 or rows[0].get("id") != marker_id:
                raise TypeError
            await self._assert_permit(permit)
            try:
                await self._call("delete", control, ids=[marker_id])
            except Exception:
                pass
            remaining = await self._call(
                "get", control, ids=[marker_id],
                output_fields=["id"], consistency_level="Strong",
            )
            if remaining:
                raise TypeError
        except MilvusKnowledgeError as error:
            if error.code == "KNOWLEDGE_MUTATION_LEASE_INVALID":
                raise
            raise MilvusKnowledgeError(
                "KNOWLEDGE_RETIREMENT_MARKER_CLEAR_FAILED"
            ) from None
        except Exception:
            raise MilvusKnowledgeError(
                "KNOWLEDGE_RETIREMENT_MARKER_CLEAR_FAILED"
            ) from None

    @staticmethod
    def _write_count(result: object, operation: str) -> int | None:
        if not isinstance(result, dict):
            return None
        value = result.get(f"{operation}_count")
        return int(value) if isinstance(value, int) else None

    async def _write_exact(self, operation: str, collection: str, rows: list[dict[str, Any]]) -> None:
        try:
            result = await self._call(operation, collection, data=rows)
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_WRITE_FAILED") from None
        if self._write_count(result, operation) != len(rows):
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_WRITE_INCOMPLETE")

    @staticmethod
    def _rows_match(
        actual: dict[str, Any], expected: dict[str, Any], *, ignore_active: bool = False,
        ignore_fence: bool = False,
    ) -> bool:
        for field in _VERIFIED_FIELDS:
            if ignore_active and field == "isActive":
                continue
            if ignore_fence and field == "mutationFence":
                continue
            left, right = actual.get(field), expected.get(field)
            if field == "embedding":
                try:
                    if (
                        MilvusKnowledgeStore._canonical_vector(left)
                        != MilvusKnowledgeStore._canonical_vector(right)
                    ):
                        return False
                except (TypeError, MilvusKnowledgeError):
                    return False
            elif left != right:
                return False
        return all(actual.get(field) == expected[field]
                   for field in ("chunkIds", "expectedChunkCount") if field in expected)

    async def _get_rows(
        self, collection: str, ids: list[str], *,
        output_fields: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for start in range(0, len(ids), _VERIFY_BATCH_SIZE):
            try:
                result.extend(await self._call(
                    "get", collection, ids=ids[start:start + _VERIFY_BATCH_SIZE],
                    output_fields=output_fields or ["*"], consistency_level="Strong",
                ))
            except Exception:
                raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        return result

    async def _verify_rows(
        self, collection: str, expected: list[dict[str, Any]], *, ignore_active: bool = False,
        ignore_fence: bool = False,
    ) -> bool:
        actual = await self._get_rows(collection, [row["id"] for row in expected])
        by_id = {row.get("id"): row for row in actual}
        return len(by_id) == len(expected) and all(
            row["id"] in by_id
            and self._rows_match(
                by_id[row["id"]], row, ignore_active=ignore_active,
                ignore_fence=ignore_fence,
            )
            for row in expected
        )

    async def _document_rows(self, collection: str, document_id: str) -> list[dict[str, Any]]:
        iterator: Any = None
        try:
            iterator = await self._call(
                "query_iterator", collection, batch_size=_VERIFY_BATCH_SIZE,
                limit=_MAX_DOCUMENT_HISTORY_RECORDS + 1,
                filter=self._document_filter(document_id), output_fields=["*"],
                consistency_level="Strong",
            )
            rows: list[dict[str, Any]] = []
            while True:
                batch = await self._thread_call(iterator.next)
                if not batch:
                    break
                rows.extend(batch)
                if len(rows) > _MAX_DOCUMENT_HISTORY_RECORDS:
                    raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_HISTORY_LIMIT_EXCEEDED")
            return rows
        except MilvusKnowledgeError:
            raise
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        finally:
            if iterator is not None:
                try:
                    await self._thread_call(iterator.close)
                except Exception:
                    pass

    async def _manifest_rows(
        self, collection: str, document_id: str,
    ) -> list[dict[str, Any]]:
        iterator: Any = None
        try:
            iterator = await self._call(
                "query_iterator", collection, batch_size=_VERIFY_BATCH_SIZE,
                limit=_MAX_DOCUMENT_HISTORY_RECORDS + 1,
                filter=(
                    f'{self._document_filter(document_id)} and '
                    'recordType == "manifest"'
                ),
                output_fields=[
                    "id", "recordType", "documentId", "documentVersion",
                    "isDeleted", "chunkIds", "expectedChunkCount",
                ],
                consistency_level="Strong",
            )
            rows: list[dict[str, Any]] = []
            while True:
                batch = await self._thread_call(iterator.next)
                if not batch:
                    break
                rows.extend(batch)
                if len(rows) > _MAX_DOCUMENT_HISTORY_RECORDS:
                    raise MilvusKnowledgeError(
                        "KNOWLEDGE_DOCUMENT_HISTORY_LIMIT_EXCEEDED"
                    )
            return rows
        except MilvusKnowledgeError:
            raise
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        finally:
            if iterator is not None:
                try:
                    await self._thread_call(iterator.close)
                except Exception:
                    pass

    async def _converge_document(
        self, collection: str, document_id: str, permit: MutationPermit
    ) -> int:
        for _ in range(5):
            rows = await self._document_rows(collection, document_id)
            manifests = [row for row in rows if row.get("recordType") == "manifest"]
            tombstoned = await self._tombstoned_versions(
                document_id, {int(row["documentVersion"]) for row in manifests}
            )
            manifests = [row for row in manifests
                         if int(row["documentVersion"]) not in tombstoned]
            if not manifests:
                raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
            highest = max(int(row["documentVersion"]) for row in manifests)
            candidates = [row for row in manifests if int(row["documentVersion"]) == highest]
            identities = {(row.get("etlVersion"), row.get("embeddingModelVersion"),
                           row.get("documentContentHash")) for row in candidates}
            if len(candidates) != 1 or len(identities) != 1:
                raise MilvusKnowledgeError("KNOWLEDGE_VERSION_CONFLICT")
            manifest = candidates[0]
            target_ids = set(manifest.get("chunkIds", []))
            chunks = [row for row in rows if row.get("recordType") == "chunk"
                      and row.get("id") in target_ids]
            if len(chunks) != manifest.get("expectedChunkCount") or {r.get("id") for r in chunks} != target_ids:
                raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
            updates = [{**row, "isActive": row.get("recordType") == "chunk"
                        and row.get("id") in target_ids} for row in rows]
            await self._assert_permit(permit)
            await self._write_exact("upsert", collection, updates)
            verified = await self._document_rows(collection, document_id)
            latest = max(int(row["documentVersion"]) for row in verified
                         if row.get("recordType") == "manifest")
            active_ids = {row.get("id") for row in verified
                          if row.get("recordType") == "chunk" and row.get("isActive")}
            if latest == highest and active_ids == target_ids:
                return highest
        raise MilvusKnowledgeError("KNOWLEDGE_ACTIVATION_UNCERTAIN")

    async def _ensure_alias(self, collection: str) -> None:
        target = await self._alias_target()
        if target is not None:
            if target != collection:
                raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH")
            return
        try:
            await self._call("create_alias", collection, self._alias)
            return
        except Exception:
            pass
        # A concurrent initializer is success only when it selected the same collection.
        if await self._alias_target() != collection:
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH")

    async def upsert_document_version(
        self, document: IndexedDocument, *, lease: KnowledgeMutationLease | None = None,
    ) -> IndexResult:
        dimension = self._validate_document(document)
        scope = f"document:{document.document_id}"
        async with self._mutation(lease, scope=scope, operation="upsert") as permit:
            return await self._upsert_document_version(document, dimension, permit)

    async def _upsert_document_version(
        self, document: IndexedDocument, dimension: int, permit: MutationPermit,
    ) -> IndexResult:
        canonical = self._collection_name(document.embedding_model_version, dimension)
        async with self._write_lock:
            if document.document_version in await self._tombstoned_versions(
                document.document_id, {document.document_version}
            ):
                raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_DISABLED")
            target = await self._alias_target()
            collection = target or canonical
            if target is None:
                await self._assert_permit(permit)
                await self._ensure_collection(
                    collection, document.embedding_model_version, dimension, permit.fence
                )
            else:
                await self._validate_collection(collection, document.embedding_model_version, dimension)
            existing = await self._document_rows(collection, document.document_id)
            manifests = [row for row in existing if row.get("recordType") == "manifest"]
            if manifests and max(int(row["documentVersion"]) for row in manifests) > document.document_version:
                raise MilvusKnowledgeError("KNOWLEDGE_STALE_VERSION")
            chunks = self._chunk_rows(
                document, dimension, active=False, fence=permit.fence
            )
            manifest = self._manifest_row(
                document, dimension, chunks, fence=permit.fence
            )
            same = [row for row in manifests if int(row["documentVersion"]) == document.document_version]
            idempotent = False
            if same:
                if any(row.get("isDeleted") for row in same):
                    raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_DISABLED")
                fields = ("etlVersion", "embeddingModelVersion", "documentContentHash",
                          "contentHash", "chunkIds", "expectedChunkCount")
                if len(same) != 1 or any(same[0].get(field) != manifest.get(field) for field in fields):
                    raise MilvusKnowledgeError("KNOWLEDGE_VERSION_CONFLICT")
                if not await self._verify_rows(
                    collection, chunks, ignore_active=True, ignore_fence=True
                ):
                    raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
                active_ids = {row.get("id") for row in existing
                              if row.get("recordType") == "chunk" and row.get("isActive")}
                idempotent = active_ids == set(manifest["chunkIds"])
            else:
                await self._assert_permit(permit)
                await self._write_exact("upsert", collection, chunks)
                if not await self._verify_rows(collection, chunks):
                    raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
                await self._assert_permit(permit)
                await self._write_exact("upsert", collection, [manifest])
                if not await self._verify_rows(collection, [manifest]):
                    raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
            active_version = await self._converge_document(
                collection, document.document_id, permit
            )
            await self._assert_permit(permit)
            await self._ensure_alias(collection)
            if active_version > document.document_version:
                raise MilvusKnowledgeError("KNOWLEDGE_STALE_VERSION")
            return IndexResult(document.document_id, document.document_version,
                               len(document.chunks), collection, idempotent)

    async def delete_document(
        self, document_id: str, document_version: int, *,
        lease: KnowledgeMutationLease | None = None,
    ) -> None:
        if not self._valid_id(document_id) or not isinstance(document_version, int) or document_version < 1:
            raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_INVALID")
        scope = f"document:{document_id}"
        async with self._mutation(lease, scope=scope, operation="delete") as permit:
            await self._delete_document(document_id, document_version, permit)

    async def _delete_document(
        self, document_id: str, document_version: int, permit: MutationPermit,
    ) -> None:
        async with self._write_lock:
            await self._assert_permit(permit)
            await self._write_tombstone(document_id, document_version, permit.fence)
            target = await self._alias_target()
            if target is None:
                return
            rows = await self._document_rows(target, document_id)
            selected = [row for row in rows if int(row.get("documentVersion", -1)) == document_version]
            if selected:
                updates = [
                    {**row, "isActive": False,
                     **({"isDeleted": True} if row.get("recordType") == "manifest" else {})}
                    for row in selected
                ]
                await self._assert_permit(permit)
                await self._write_exact("upsert", target, updates)
                verified = await self._document_rows(target, document_id)
                if any(row.get("isActive") and int(row.get("documentVersion", -1)) == document_version
                       for row in verified):
                    raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_WRITE_INCOMPLETE")

    async def _current_document_state(
        self, collection: str, document_id: str,
    ) -> tuple[int, frozenset[str]] | None:
        """Prove the current version and its allowed physical row IDs."""

        manifests_source = await self._manifest_rows(collection, document_id)
        manifests: list[dict[str, Any]] = []
        versions: set[int] = set()
        for row in manifests_source:
            if row.get("recordType") != "manifest" or row.get("isDeleted") is True:
                continue
            version = row.get("documentVersion")
            if not isinstance(version, int) or isinstance(version, bool) or version < 1:
                continue
            manifests.append(row)
            versions.add(version)
        tombstoned = await self._tombstoned_versions(document_id, versions)
        manifests = [
            row for row in manifests
            if int(row["documentVersion"]) not in tombstoned
        ]
        if not manifests:
            return None
        highest = max(int(row["documentVersion"]) for row in manifests)
        current = [
            row for row in manifests if int(row["documentVersion"]) == highest
        ]
        if len(current) != 1:
            return None
        manifest = current[0]
        chunk_ids = manifest.get("chunkIds")
        expected = manifest.get("expectedChunkCount")
        if (
            not isinstance(chunk_ids, list)
            or any(not isinstance(value, str) or not value for value in chunk_ids)
            or len(chunk_ids) != len(set(chunk_ids))
            or not isinstance(expected, int)
            or isinstance(expected, bool)
            or expected != len(chunk_ids)
        ):
            return None
        chunk_rows = await self._get_rows(
            collection, chunk_ids,
            output_fields=[
                "id", "recordType", "documentId", "documentVersion", "isActive",
            ],
        )
        by_id = {
            row.get("id"): row for row in chunk_rows
            if row.get("recordType") == "chunk" and row.get("id") in chunk_ids
        }
        if set(by_id) != set(chunk_ids):
            return None
        if any(
            row.get("documentId") != document_id
            or row.get("documentVersion") != highest
            or row.get("isActive") is not True
            for row in by_id.values()
        ):
            return None
        return highest, frozenset(chunk_ids)

    async def search(self, vector: list[float], limit: int) -> list[RetrievedChunk]:
        if (not isinstance(vector, list) or not vector or not isinstance(limit, int)
                or not 1 <= limit <= 100
                or not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in vector)):
            raise MilvusKnowledgeError("KNOWLEDGE_SEARCH_INVALID")
        try:
            collection = await self._alias_target()
        except MilvusKnowledgeError:
            raise MilvusKnowledgeError(
                "CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE"
            ) from None
        if collection is None:
            raise MilvusKnowledgeError("CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE")
        try:
            result = await self._call(
                "search", collection, data=[[float(v) for v in vector]],
                filter='recordType == "chunk" and isActive == true', limit=limit,
                output_fields=_OUTPUT_FIELDS,
                search_params={"metric_type": "COSINE", "params": {}},
                consistency_level="Strong",
            )
            hits: list[tuple[str, RetrievedChunk]] = []
            for hit in result[0] if result else []:
                entity = hit.get("entity", hit)
                record_id = hit.get("id", entity.get("id"))
                if not isinstance(record_id, str) or not record_id:
                    raise ValueError
                score = float(hit.get("distance", hit.get("score")))
                distance = 1.0 - score
                if not math.isfinite(distance) or not math.isfinite(score):
                    raise ValueError
                hits.append((record_id, RetrievedChunk(
                    str(entity["chunkId"]), str(entity["documentId"]),
                    int(entity["documentVersion"]), int(entity["chunkIndex"]),
                    str(entity["content"]), str(entity["fileName"]), str(entity["fileType"]),
                    str(entity["sourceLocator"]), str(entity["contentHash"]), distance, score,
                    bool(entity.get("isActive", True)),
                    None,
                )))
            current_states = {
                document_id: await self._current_document_state(
                    collection, document_id
                )
                for document_id in dict.fromkeys(item.document_id for _, item in hits)
            }
            return [
                replace(item, current_document_version=state[0])
                for record_id, item in hits
                if (state := current_states[item.document_id]) is not None
                and item.document_version == state[0]
                and record_id in state[1]
            ]
        except MilvusKnowledgeError:
            raise
        except Exception:
            raise MilvusKnowledgeError("CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE") from None

    async def _switch_alias(self, old: str | None, new: str) -> None:
        try:
            await self._call("create_alias" if old is None else "alter_alias", new, self._alias)
            return
        except Exception:
            pass
        try:
            current = await self._alias_target()
        except MilvusKnowledgeError:
            raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN") from None
        if old is None:
            if current == new:
                return
            if current is None:
                raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_FAILED")
            raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN")
        if current == old:
            raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_FAILED")
        if current != new:
            raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN")
        try:
            await self._call("alter_alias", old, self._alias)
        except Exception:
            try:
                after = await self._alias_target()
            except MilvusKnowledgeError:
                raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN") from None
            if after == old:
                raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_FAILED")
            raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN")
        try:
            after = await self._alias_target()
        except MilvusKnowledgeError:
            raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN") from None
        if after != old:
            raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN")
        raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_FAILED")

    async def rebuild_collection(
        self, documents: AsyncIterator[IndexedDocument], *,
        lease: KnowledgeMutationLease | None = None,
        plan: RebuildPlan,
    ) -> RebuildResult:
        self._validate_rebuild_plan(plan)
        scope = f"collection:{self._alias}"
        async with self._mutation(lease, scope=scope, operation="rebuild") as permit:
            assert lease is not None
            return await self._rebuild_collection(documents, permit, lease, plan)

    async def _rebuild_collection(
        self, documents: AsyncIterator[IndexedDocument], permit: MutationPermit,
        lease: KnowledgeMutationLease, plan: RebuildPlan,
    ) -> RebuildResult:
        async with self._write_lock:
            old = await self._alias_target()
            model = plan.embedding_model_version
            dimension = plan.embedding_dimension
            if plan.document_count == 0 and old is not None:
                description = await self._describe_collection(old)
                metadata = self._metadata(description)
                inherited_model = metadata.get("embeddingModelVersion")
                inherited_dimension = metadata.get("embeddingDimension")
                if not isinstance(inherited_model, str) or not isinstance(
                    inherited_dimension, int
                ):
                    raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_MISMATCH")
                await self._validate_collection(
                    old, inherited_model, inherited_dimension
                )
                model, dimension = inherited_model, inherited_dimension
            collection = self._rebuild_collection_name(
                model, dimension, lease, plan
            )
            if old == collection:
                await self._validate_rebuild_collection(
                    collection, model, dimension, lease, plan
                )
                completion = await self._validated_rebuild_completion(
                    collection, model, dimension, lease, plan
                )
                return RebuildResult(
                    collection, plan.document_count,
                    int(completion["actualChunkCount"]), True,
                )

            switched = False
            cleanup_candidate = False
            try:
                if await self._call("has_collection", collection):
                    await self._validate_rebuild_collection(
                        collection, model, dimension, lease, plan
                    )
                    await self._assert_permit(permit)
                    await self._call("drop_collection", collection)
                cleanup_candidate = True
                await self._assert_permit(permit)
                await self._ensure_collection(
                    collection, model, dimension, permit.fence,
                    rebuild=(lease, plan),
                )
                await self._validate_rebuild_collection(
                    collection, model, dimension, lease, plan
                )

                document_count = 0
                chunk_count = 0
                document_ids: set[str] = set()
                fingerprint = RebuildFingerprint(self._alias, plan.etl_version)
                async for document in documents:
                    document_count += 1
                    if document_count > plan.document_count:
                        raise MilvusKnowledgeError("KNOWLEDGE_REBUILD_PLAN_INVALID")
                    document_dimension = self._validate_document(document)
                    if (
                        document_dimension != dimension
                        or document.embedding_model_version != model
                    ):
                        raise MilvusKnowledgeError(
                            "KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH"
                        )
                    if document.etl_version != plan.etl_version:
                        raise MilvusKnowledgeError("KNOWLEDGE_REBUILD_PLAN_INVALID")
                    if document.document_id in document_ids:
                        raise MilvusKnowledgeError("KNOWLEDGE_VERSION_CONFLICT")
                    document_ids.add(document.document_id)
                    fingerprint.add_document(
                        document_id=document.document_id,
                        document_version=document.document_version,
                        file_name=document.file_name,
                        file_type=document.file_type,
                        content_hash=document.content_hash,
                    )
                    if document.document_version in await self._tombstoned_versions(
                        document.document_id, {document.document_version}
                    ):
                        raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_DISABLED")
                    chunk_ids: list[dict[str, str]] = []
                    for start in range(
                        0, len(document.chunks), _REBUILD_INSERT_BATCH_SIZE
                    ):
                        rows = self._chunk_rows(
                            document, dimension, active=True, fence=permit.fence,
                            chunks=document.chunks[
                                start:start + _REBUILD_INSERT_BATCH_SIZE
                            ],
                        )
                        chunk_ids.extend({"id": row["id"]} for row in rows)
                        await self._assert_permit(permit)
                        await self._write_exact("insert", collection, rows)
                        if not await self._verify_rows(collection, rows):
                            raise MilvusKnowledgeError(
                                "KNOWLEDGE_STAGING_INCOMPLETE"
                            )
                        chunk_count += len(rows)
                    manifest = self._manifest_row(
                        document, dimension, chunk_ids, fence=permit.fence
                    )
                    await self._assert_permit(permit)
                    await self._write_exact("insert", collection, [manifest])
                    if not await self._verify_rows(collection, [manifest]):
                        raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")

                if (
                    document_count != plan.document_count
                    or fingerprint.hexdigest() != plan.document_fingerprint
                ):
                    raise MilvusKnowledgeError("KNOWLEDGE_REBUILD_PLAN_INVALID")
                completion = self._rebuild_completion_row(
                    model, dimension, lease, plan, document_count, chunk_count
                )
                await self._assert_permit(permit)
                await self._write_exact("insert", collection, [completion])
                if not await self._verify_rows(collection, [completion]):
                    raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
                try:
                    await self._clear_retirement_marker_for_publish(
                        collection, permit,
                    )
                    await self._assert_permit(permit)
                    await self._switch_alias(old, collection)
                    switched = True
                except MilvusKnowledgeError:
                    raise
                await self._cleanup_retired_collections(
                    current=collection, just_replaced=old,
                    model=model, dimension=dimension, permit=permit,
                )
                return RebuildResult(
                    collection, document_count, chunk_count, False
                )
            finally:
                if cleanup_candidate and not switched:
                    await self._cleanup_rebuild_candidate(
                        collection, lease, plan, model, dimension,
                        known_absent_before_create=True,
                    )

    async def ping(self) -> bool:
        try:
            await self._call("list_collections")
            return True
        except Exception:
            return False

    async def close(self) -> None:
        close = getattr(self._client, "close", None)
        if close is not None:
            await asyncio.to_thread(close)
