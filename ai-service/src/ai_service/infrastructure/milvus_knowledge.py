from __future__ import annotations

import asyncio
import hashlib
import math
import re
import uuid
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from pymilvus import MilvusClient

from ai_service.config import Settings


SCHEMA_VERSION = 1
_OUTPUT_FIELDS = [
    "chunkId", "documentId", "documentVersion", "chunkIndex", "etlVersion",
    "embeddingModelVersion", "fileName", "fileType", "sourceLocator", "content",
    "contentHash", "embeddingDimension", "schemaVersion",
]


class MilvusKnowledgeError(RuntimeError):
    """Stable storage error that does not retain Milvus response details."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


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
    async def upsert_document_version(self, document: IndexedDocument) -> IndexResult: ...

    async def delete_document(self, document_id: str, document_version: int) -> None: ...

    async def search(self, vector: list[float], limit: int) -> list[RetrievedChunk]: ...

    async def rebuild_collection(
        self, documents: AsyncIterator[IndexedDocument]
    ) -> RebuildResult: ...

    async def ping(self) -> bool: ...


class MilvusKnowledgeStore:
    """Version-aware Milvus adapter with verified staging and stable alias reads."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[..., Any] = MilvusClient,
        schema_version: int = SCHEMA_VERSION,
    ) -> None:
        self._alias = settings.milvus_collection_alias
        self._schema_version = schema_version
        try:
            self._client = client_factory(
                uri=settings.milvus_uri,
                token=settings.milvus_token,
                db_name=settings.milvus_database,
            )
        except Exception:
            raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE") from None
        self._write_lock = asyncio.Lock()

    async def _call(self, method: str, *args: object, **kwargs: object) -> Any:
        function = getattr(self._client, method)
        return await asyncio.to_thread(function, *args, **kwargs)

    @staticmethod
    def _validate_document(document: IndexedDocument) -> int:
        if (
            not document.document_id or not document.etl_version
            or not document.embedding_model_version or document.document_version < 1
            or not document.file_name or not document.file_type or not document.content_hash
            or not document.chunks
        ):
            raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_INVALID")
        dimension: int | None = None
        chunk_ids: set[str] = set()
        for chunk in document.chunks:
            if (
                not chunk.chunk_id or chunk.chunk_id in chunk_ids or chunk.chunk_index < 0
                or not chunk.content or not chunk.source_locator or not chunk.content_hash
                or not isinstance(chunk.embedding, Sequence) or not chunk.embedding
            ):
                raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_INVALID")
            chunk_ids.add(chunk.chunk_id)
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

    async def _alias_target(self) -> str | None:
        try:
            description = await self._call("describe_alias", self._alias)
        except Exception:
            return None
        return str(description.get("collection_name") or description.get("collection") or "") or None

    async def _ensure_collection(self, name: str, dimension: int) -> None:
        if not await self._call("has_collection", name):
            await self._call(
                "create_collection", name, dimension=dimension, primary_field_name="id",
                id_type="string", vector_field_name="embedding", metric_type="COSINE",
                auto_id=False, enable_dynamic_field=True, consistency_level="Strong",
            )

    def _rows(self, document: IndexedDocument, dimension: int, *, active: bool) -> list[dict[str, Any]]:
        identity = (
            f"{document.document_id}\0{document.document_version}\0{document.etl_version}"
            f"\0{document.embedding_model_version}"
        )
        rows: list[dict[str, Any]] = []
        for chunk in document.chunks:
            row_id = hashlib.sha256(f"{identity}\0{chunk.chunk_id}".encode()).hexdigest()
            rows.append({
                "id": row_id, "chunkId": chunk.chunk_id,
                "documentId": document.document_id,
                "documentVersion": document.document_version,
                "chunkIndex": chunk.chunk_index, "etlVersion": document.etl_version,
                "embeddingModelVersion": document.embedding_model_version,
                "fileName": document.file_name, "fileType": document.file_type,
                "sourceLocator": chunk.source_locator, "content": chunk.content,
                "contentHash": chunk.content_hash, "documentContentHash": document.content_hash,
                "embeddingDimension": dimension, "schemaVersion": self._schema_version,
                "isActive": active, "embedding": [float(value) for value in chunk.embedding],
            })
        return rows

    @staticmethod
    def _document_filter(document_id: str) -> str:
        safe = document_id.replace("\\", "\\\\").replace('"', '\\"')
        return f'documentId == "{safe}"'

    async def upsert_document_version(self, document: IndexedDocument) -> IndexResult:
        dimension = self._validate_document(document)
        collection = self._collection_name(document.embedding_model_version, dimension)
        async with self._write_lock:
            try:
                target = await self._alias_target()
                if target is not None and target != collection:
                    raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
                await self._ensure_collection(collection, dimension)
                existing = await self._call(
                    "query", collection, filter=self._document_filter(document.document_id),
                    output_fields=["*"], consistency_level="Strong",
                )
                versions = [int(row["documentVersion"]) for row in existing]
                if versions and max(versions) > document.document_version:
                    raise MilvusKnowledgeError("KNOWLEDGE_STALE_VERSION")
                same_version = [row for row in existing
                                if int(row["documentVersion"]) == document.document_version]
                if same_version:
                    identity_matches = all(
                        row.get("etlVersion") == document.etl_version
                        and row.get("embeddingModelVersion") == document.embedding_model_version
                        and row.get("documentContentHash") == document.content_hash
                        for row in same_version
                    )
                    expected_ids = {chunk.chunk_id for chunk in document.chunks}
                    actual_ids = {row.get("chunkId") for row in same_version}
                    if not identity_matches or not actual_ids <= expected_ids:
                        raise MilvusKnowledgeError("KNOWLEDGE_VERSION_CONFLICT")
                    if actual_ids == expected_ids and all(row.get("isActive") for row in same_version):
                        return IndexResult(document.document_id, document.document_version,
                                           len(document.chunks), collection, True)

                rows = self._rows(document, dimension, active=False)
                await self._call("upsert", collection, data=rows)
                staged = await self._call(
                    "query", collection,
                    filter=(f'{self._document_filter(document.document_id)} and '
                            f'documentVersion == {document.document_version}'),
                    output_fields=["*"], consistency_level="Strong",
                )
                if {row.get("id") for row in staged} != {row["id"] for row in rows}:
                    raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
                await self._call("upsert", collection, data=[{**row, "isActive": True} for row in rows])
                older = [row for row in existing if row.get("isActive")]
                if older:
                    await self._call("upsert", collection,
                                     data=[{**row, "isActive": False} for row in older])
                if target is None:
                    await self._call("create_alias", collection, self._alias)
                return IndexResult(document.document_id, document.document_version,
                                   len(document.chunks), collection)
            except MilvusKnowledgeError:
                raise
            except Exception:
                raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_WRITE_FAILED") from None

    async def delete_document(self, document_id: str, document_version: int) -> None:
        if not document_id or document_version < 1:
            raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_INVALID")
        async with self._write_lock:
            try:
                target = await self._alias_target()
                if target is None:
                    return
                rows = await self._call(
                    "query", target,
                    filter=(f'{self._document_filter(document_id)} and '
                            f'documentVersion == {document_version}'),
                    output_fields=["*"], consistency_level="Strong",
                )
                if rows:
                    await self._call("upsert", target,
                                     data=[{**row, "isActive": False} for row in rows])
            except MilvusKnowledgeError:
                raise
            except Exception:
                raise MilvusKnowledgeError("KNOWLEDGE_VECTOR_WRITE_FAILED") from None

    async def search(self, vector: list[float], limit: int) -> list[RetrievedChunk]:
        if (
            not isinstance(vector, list) or not vector or not 1 <= limit <= 100
            or not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in vector)
        ):
            raise MilvusKnowledgeError("KNOWLEDGE_SEARCH_INVALID")
        try:
            result = await self._call(
                "search", self._alias, data=[[float(value) for value in vector]],
                filter="isActive == true", limit=limit, output_fields=_OUTPUT_FIELDS,
                search_params={"metric_type": "COSINE", "params": {}},
                consistency_level="Strong",
            )
            hits = result[0] if result else []
            output: list[RetrievedChunk] = []
            for hit in hits:
                entity = hit.get("entity", hit)
                distance = float(hit.get("distance", hit.get("score", 0.0)))
                score = float(hit.get("score", 1.0 - distance))
                if not math.isfinite(distance) or not math.isfinite(score):
                    raise ValueError("non-finite search score")
                output.append(RetrievedChunk(
                    chunk_id=str(entity["chunkId"]), document_id=str(entity["documentId"]),
                    document_version=int(entity["documentVersion"]),
                    chunk_index=int(entity["chunkIndex"]), content=str(entity["content"]),
                    file_name=str(entity["fileName"]), file_type=str(entity["fileType"]),
                    source_locator=str(entity["sourceLocator"]),
                    content_hash=str(entity["contentHash"]), distance=distance, score=score,
                ))
            return output
        except MilvusKnowledgeError:
            raise
        except Exception:
            raise MilvusKnowledgeError("CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE") from None

    async def rebuild_collection(
        self, documents: AsyncIterator[IndexedDocument]
    ) -> RebuildResult:
        staged_documents = [document async for document in documents]
        if not staged_documents:
            raise MilvusKnowledgeError("KNOWLEDGE_DOCUMENT_INVALID")
        dimensions = {self._validate_document(document) for document in staged_documents}
        models = {document.embedding_model_version for document in staged_documents}
        if len(dimensions) != 1 or len(models) != 1:
            raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
        dimension = dimensions.pop()
        model = models.pop()
        suffix = f"_staging_{uuid.uuid4().hex[:12]}"
        collection = self._collection_name(model, dimension, suffix=suffix)
        async with self._write_lock:
            old_target = await self._alias_target()
            alias_changed = False
            try:
                await self._ensure_collection(collection, dimension)
                rows: list[dict[str, Any]] = []
                seen_documents: set[str] = set()
                for document in staged_documents:
                    if document.document_id in seen_documents:
                        raise MilvusKnowledgeError("KNOWLEDGE_VERSION_CONFLICT")
                    seen_documents.add(document.document_id)
                    rows.extend(self._rows(document, dimension, active=True))
                await self._call("insert", collection, data=rows)
                staged = await self._call("query", collection, filter="",
                                          output_fields=["*"], consistency_level="Strong")
                expected_by_id = {row["id"]: row for row in rows}
                staged_by_id = {row.get("id"): row for row in staged}
                verified_fields = {
                    "id", "chunkId", "documentId", "documentVersion", "chunkIndex",
                    "etlVersion", "embeddingModelVersion", "fileName", "fileType",
                    "sourceLocator", "content", "contentHash", "documentContentHash",
                    "embeddingDimension", "schemaVersion", "isActive",
                }
                if (
                    staged_by_id.keys() != expected_by_id.keys()
                    or any(
                        actual.get(field) != expected[field]
                        for row_id, expected in expected_by_id.items()
                        for actual in [staged_by_id[row_id]]
                        for field in verified_fields
                    )
                ):
                    raise MilvusKnowledgeError("KNOWLEDGE_STAGING_INCOMPLETE")
                if old_target is None:
                    await self._call("create_alias", collection, self._alias)
                else:
                    await self._call("alter_alias", collection, self._alias)
                alias_changed = True
                return RebuildResult(collection, len(staged_documents), len(rows))
            except MilvusKnowledgeError:
                raise
            except Exception:
                if old_target is not None:
                    try:
                        await self._call("alter_alias", old_target, self._alias)
                    except Exception:
                        pass
                raise MilvusKnowledgeError("KNOWLEDGE_ALIAS_SWITCH_FAILED") from None
            finally:
                if not alias_changed:
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
