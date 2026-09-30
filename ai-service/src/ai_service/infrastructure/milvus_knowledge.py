from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any, AsyncContextManager, Protocol

from pymilvus import MilvusClient

from ai_service.config import Settings

SCHEMA_VERSION = 1
_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CHUNK_ID_PATTERN = re.compile(r"[A-Za-z0-9_.:-]{1,512}")
_VERIFY_BATCH_SIZE = 256
_MAX_DOCUMENT_HISTORY_RECORDS = 10_000
_OUTPUT_FIELDS = [
    "chunkId", "documentId", "documentVersion", "chunkIndex", "etlVersion",
    "embeddingModelVersion", "fileName", "fileType", "sourceLocator", "content",
    "contentHash", "embeddingDimension", "schemaVersion",
]
_VERIFIED_FIELDS = {
    "id", "recordType", "chunkId", "documentId", "documentVersion", "chunkIndex",
    "etlVersion", "embeddingModelVersion", "fileName", "fileType", "sourceLocator",
    "content", "contentHash", "documentContentHash", "embeddingDimension",
    "schemaVersion", "mutationFence", "isActive", "embedding",
}


class MilvusKnowledgeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class KnowledgeMutationLease:
    scope: str
    operation_id: str
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


@dataclass(frozen=True, slots=True)
class RebuildResult:
    collection_name: str
    document_count: int
    chunk_count: int


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
        self._schema_version = schema_version
        try:
            self._client = client_factory(
                uri=settings.milvus_uri, token=settings.milvus_token,
                db_name=settings.milvus_database,
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
        if (
            lease.scope != scope or not lease.operation_id
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
        return await asyncio.to_thread(getattr(self._client, method), *args, **kwargs)

    @staticmethod
    def _valid_id(value: str) -> bool:
        return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None

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
            try:
                vector = [float(value) for value in chunk.embedding]
            except (TypeError, ValueError, OverflowError):
                raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_INVALID_VECTOR") from None
            if not all(math.isfinite(value) for value in vector):
                raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_INVALID_VECTOR")
            if dimension is None:
                dimension = len(vector)
            elif len(vector) != dimension:
                raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
        assert dimension is not None
        return dimension

    def _collection_name(self, model: str, dimension: int, *, suffix: str = "") -> str:
        fingerprint = hashlib.sha256(
            f"customer-service\0{model}\0{dimension}\0{self._schema_version}".encode()
        ).hexdigest()[:12]
        prefix = re.sub(r"[^A-Za-z0-9_]", "_", self._alias)[:80].strip("_") or "knowledge"
        return f"{prefix}_{fingerprint}{suffix}"

    def _collection_metadata(self, model: str, dimension: int, fence: int) -> str:
        return json.dumps({
            "kind": "customer-service-knowledge",
            "embeddingModelVersion": model,
            "embeddingDimension": dimension,
            "schemaVersion": self._schema_version,
            "mutationFence": fence,
        }, sort_keys=True, separators=(",", ":"))

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
        self, name: str, model: str, dimension: int, fence: int
    ) -> None:
        try:
            if not await self._call("has_collection", name):
                try:
                    await self._call(
                        "create_collection", name, dimension=dimension, primary_field_name="id",
                        id_type="string", vector_field_name="embedding", metric_type="COSINE",
                        auto_id=False, max_length=64, enable_dynamic_field=True,
                        consistency_level="Strong",
                        description=self._collection_metadata(model, dimension, fence),
                    )
                except Exception:
                    if not await self._call("has_collection", name):
                        raise
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        await self._validate_collection(name, model, dimension)

    @staticmethod
    def _identity(document: IndexedDocument) -> str:
        return (
            f"{document.document_id}\0{document.document_version}\0{document.etl_version}"
            f"\0{document.embedding_model_version}"
        )

    def _chunk_rows(
        self, document: IndexedDocument, dimension: int, *, active: bool, fence: int
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
            "embedding": [float(value) for value in chunk.embedding],
        } for chunk in document.chunks]

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

    @staticmethod
    def _document_filter(document_id: str) -> str:
        return f'documentId == "{document_id}"'

    def _control_collection_name(self) -> str:
        prefix = re.sub(r"[^A-Za-z0-9_]", "_", self._alias)[:100].strip("_") or "knowledge"
        return f"{prefix}_mutation_control_v1"

    @staticmethod
    def _tombstone_id(document_id: str, document_version: int) -> str:
        return hashlib.sha256(
            f"knowledge-tombstone\0{document_id}\0{document_version}".encode()
        ).hexdigest()

    async def _ensure_control_collection(self, fence: int) -> str:
        name = self._control_collection_name()
        try:
            if not await self._call("has_collection", name):
                description = json.dumps({
                    "kind": "customer-service-knowledge-mutation-control",
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
                    if [float(value) for value in left] != [float(value) for value in right]:
                        return False
                except (TypeError, ValueError):
                    return False
            elif left != right:
                return False
        return all(actual.get(field) == expected[field]
                   for field in ("chunkIds", "expectedChunkCount") if field in expected)

    async def _get_rows(self, collection: str, ids: list[str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for start in range(0, len(ids), _VERIFY_BATCH_SIZE):
            try:
                result.extend(await self._call(
                    "get", collection, ids=ids[start:start + _VERIFY_BATCH_SIZE],
                    output_fields=["*"], consistency_level="Strong",
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
                batch = await asyncio.to_thread(iterator.next)
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
                    await asyncio.to_thread(iterator.close)
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

    async def search(self, vector: list[float], limit: int) -> list[RetrievedChunk]:
        if (not isinstance(vector, list) or not vector or not isinstance(limit, int)
                or not 1 <= limit <= 100
                or not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in vector)):
            raise MilvusKnowledgeError("KNOWLEDGE_SEARCH_INVALID")
        try:
            result = await self._call(
                "search", self._alias, data=[[float(v) for v in vector]],
                filter='recordType == "chunk" and isActive == true', limit=limit,
                output_fields=_OUTPUT_FIELDS,
                search_params={"metric_type": "COSINE", "params": {}},
                consistency_level="Strong",
            )
            output: list[RetrievedChunk] = []
            for hit in result[0] if result else []:
                entity = hit.get("entity", hit)
                distance = float(hit.get("distance", hit.get("score", 0.0)))
                score = float(hit.get("score", 1.0 - distance))
                if not math.isfinite(distance) or not math.isfinite(score):
                    raise ValueError
                output.append(RetrievedChunk(
                    str(entity["chunkId"]), str(entity["documentId"]),
                    int(entity["documentVersion"]), int(entity["chunkIndex"]),
                    str(entity["content"]), str(entity["fileName"]), str(entity["fileType"]),
                    str(entity["sourceLocator"]), str(entity["contentHash"]), distance, score,
                ))
            return output
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
    ) -> RebuildResult:
        scope = f"collection:{self._alias}"
        async with self._mutation(lease, scope=scope, operation="rebuild") as permit:
            docs = [document async for document in documents]
            return await self._rebuild_collection(docs, permit)

    async def _rebuild_collection(
        self, docs: list[IndexedDocument], permit: MutationPermit,
    ) -> RebuildResult:
        if not docs:
            raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_INVALID")
        dimensions = {self._validate_document(document) for document in docs}
        models = {document.embedding_model_version for document in docs}
        if len(dimensions) != 1 or len(models) != 1:
            raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
        if len({document.document_id for document in docs}) != len(docs):
            raise MilvusKnowledgeError("KNOWLEDGE_VERSION_CONFLICT")
        dimension, model = dimensions.pop(), models.pop()
        collection = self._collection_name(model, dimension,
                                           suffix=f"_staging_{uuid.uuid4().hex[:12]}")
        async with self._write_lock:
            old = await self._alias_target()
            safe_to_drop, switched = True, False
            try:
                await self._assert_permit(permit)
                await self._ensure_collection(
                    collection, model, dimension, permit.fence
                )
                chunks: list[dict[str, Any]] = []
                manifests: list[dict[str, Any]] = []
                for document in docs:
                    if document.document_version in await self._tombstoned_versions(
                        document.document_id, {document.document_version}
                    ):
                        raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_DISABLED")
                    rows = self._chunk_rows(
                        document, dimension, active=True, fence=permit.fence
                    )
                    chunks.extend(rows)
                    manifests.append(self._manifest_row(
                        document, dimension, rows, fence=permit.fence
                    ))
                all_rows = [*chunks, *manifests]
                for start in range(0, len(all_rows), _VERIFY_BATCH_SIZE):
                    await self._assert_permit(permit)
                    await self._write_exact("insert", collection,
                                            all_rows[start:start + _VERIFY_BATCH_SIZE])
                if not await self._verify_rows(collection, all_rows):
                    raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
                try:
                    await self._assert_permit(permit)
                    await self._switch_alias(old, collection)
                    switched = True
                except MilvusKnowledgeError as error:
                    if error.code == "KNOWLEDGE_ALIAS_SWITCH_UNCERTAIN":
                        safe_to_drop = False
                    raise
                return RebuildResult(collection, len(docs), len(chunks))
            finally:
                if not switched and safe_to_drop:
                    try:
                        if await self._call("has_collection", collection):
                            await self._call("drop_collection", collection)
                    except Exception:
                        pass

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
