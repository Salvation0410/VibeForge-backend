"""私有、不可变的整库候选；不写 alias、共享墓碑或源集合。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from uuid import uuid4

from ai_service.api.knowledge_publication_schemas import (
    CandidateReceipt, DocumentIdentity, PublicationContext, ReadSnapshot, ReceiptDocument,
)
from ai_service.config import Settings
from ai_service.infrastructure.milvus_knowledge import (
    IndexedChunk, IndexedDocument, MilvusKnowledgeError, MilvusKnowledgeStore,
    RetrievedChunk,
)

KIND = "customer-service-knowledge-publication"
RECEIPT_MAX_BYTES = 64 * 1024
_COMPLETION_ID = hashlib.sha256(b"knowledge-publication-completion-v1").hexdigest()


def _fail(code="KNOWLEDGE_PUBLICATION_INVALID"):
    raise MilvusKnowledgeError(code)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _canonical(row):
    # 仅 attempt fence 与旧协议活动位不属于不可变内容；向量以 Milvus f32 为准。
    result = {key: value for key, value in row.items()
              if key not in {"mutationFence", "isActive"}}
    result["embedding"] = MilvusKnowledgeStore._canonical_vector(row["embedding"])
    return result


def snapshot_hash(rows):
    hasher = hashlib.sha256(b"knowledge-publication-snapshot-v1\0")
    for row in sorted(rows, key=lambda item: item["id"]):
        data = _encoded(_canonical(row))
        hasher.update(len(data).to_bytes(8, "big"))
        hasher.update(data)
    return hasher.hexdigest()


def document_identity(document):
    return DocumentIdentity(
        document_id=document.document_id, document_version=document.document_version,
        index_revision=document.index_revision, activation_revision=document.activation_revision,
        sha256=document.content_hash, file_name=document.file_name,
        file_type=document.file_type.upper(),
    )


async def _drain_iterator_task(task):
    """资源获得/退出任务不能被重复取消打断，完成结果仍由调用方取回。"""
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
        except Exception:
            break
    return task.result()


class PublicationStore:
    def __init__(self, settings: Settings, store: MilvusKnowledgeStore):
        self.settings, self.store = settings, store

    def _name_valid(self, name):
        return isinstance(name, str) and re.fullmatch(
            re.escape(self.store._alias[:210]) + r"_pub_[0-9a-f]{32}", name) is not None

    async def metadata(self, collection):
        if not self._name_valid(collection):
            _fail()
        description = await self.store._describe_collection(collection)
        metadata = self.store._metadata(description)
        if (metadata.get("kind") != KIND
                or metadata.get("aliasHash") != self.store._alias_hash()
                or metadata.get("embeddingModelVersion") != self.settings.rag_embedding_model
                or metadata.get("embeddingDimension") != self.settings.rag_embedding_dimension
                or type(metadata.get("embeddingDimension")) is not int
                or metadata.get("schemaVersion") != self.store._schema_version
                or type(metadata.get("schemaVersion")) is not int
                or self.store._described_dimension(description) != self.settings.rag_embedding_dimension
                or type(metadata.get("baseEpoch")) is not int or metadata["baseEpoch"] < 0
                or type(metadata.get("mutationFence")) is not int or metadata["mutationFence"] < 1
                or not isinstance(metadata.get("operationId"), str)
                or metadata.get("operation") not in {"INDEX", "DELETE", "REBUILD"}
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", metadata["operationId"])
                or not isinstance(metadata.get("publicationId"), str)
                or not re.fullmatch(r"task_[1-9][0-9]*|bootstrap_[0-9a-f]{32}", metadata["publicationId"])):
            _fail()
        return metadata

    def budget(self, rows, *, documents=None):
        settings = self.settings
        chunks = [row for row in rows if row.get("recordType") == "chunk"]
        if documents is not None and documents > settings.rag_rebuild_max_documents:
            _fail("KNOWLEDGE_REBUILD_TOO_MANY_DOCUMENTS")
        if len(chunks) > settings.rag_rebuild_max_chunks:
            _fail("KNOWLEDGE_REBUILD_TOO_MANY_CHUNKS")
        if len(chunks) * settings.rag_embedding_dimension > settings.rag_max_embedding_elements:
            _fail("KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED")
        if sum(len(row.get("content", "").encode("utf-8")) for row in chunks) > settings.rag_rebuild_max_text_bytes:
            _fail("KNOWLEDGE_REBUILD_TEXT_BUDGET_EXCEEDED")
        # 计入 JSON 数组括号和行间逗号，完整快照不是各行裸字节之和。
        serialized_bytes = sum(len(_encoded(row)) for row in rows) + max(len(rows) - 1, 0) + 2
        if serialized_bytes > settings.rag_publication_max_bytes:
            _fail("KNOWLEDGE_PUBLICATION_BUDGET_EXCEEDED")

    async def scan(self, collection, *, filter=""):
        iterator = None
        primary_error = None
        rows = []
        try:
            # 独立持有创建任务，避免底层线程在取消后交回的 iterator 被丢弃。
            creation = asyncio.create_task(self.store._call(
                "query_iterator", collection, batch_size=100,
                limit=self.settings.rag_rebuild_max_chunks + self.settings.rag_rebuild_max_documents + 2,
                filter=filter, output_fields=["*"], consistency_level="Strong",
            ))
            try:
                iterator = await asyncio.shield(creation)
            except asyncio.CancelledError:
                try:
                    iterator = await _drain_iterator_task(creation)
                except Exception:
                    # 创建失败时没有资源可关，仍保留触发退出的原始取消。
                    pass
                raise
            while True:
                batch = await self.store._thread_call(iterator.next)
                if not batch:
                    break
                rows.extend(batch)
                # 每批检查完整序列化大小，不能仅限制 content 或向量数量。
                self.budget(rows)
                if len(rows) > self.settings.rag_rebuild_max_chunks + self.settings.rag_rebuild_max_documents + 1:
                    _fail("KNOWLEDGE_PUBLICATION_BUDGET_EXCEEDED")
            ids = [row.get("id") for row in rows]
            if any(not isinstance(value, str) for value in ids) or len(ids) != len(set(ids)):
                _fail()
            return rows
        except (MilvusKnowledgeError, asyncio.CancelledError) as error:
            primary_error = error
            raise
        except Exception:
            primary_error = MilvusKnowledgeError("KNOWLEDGE_VECTOR_STORE_UNAVAILABLE")
            raise primary_error from None
        finally:
            if iterator is not None:
                # close 无论是否失败都要先排空；清理不能替换取消或原稳定业务错误。
                closing = asyncio.create_task(self.store._thread_call(iterator.close))
                close_cancel = None
                close_failed = False
                try:
                    await asyncio.shield(closing)
                except asyncio.CancelledError as cancelled:
                    close_cancel = cancelled
                    try:
                        await _drain_iterator_task(closing)
                    except Exception:
                        close_failed = True
                except Exception:
                    close_failed = True
                if primary_error is None:
                    if close_cancel is not None:
                        raise close_cancel
                    if close_failed:
                        _fail("KNOWLEDGE_PUBLICATION_ITERATOR_CLOSE_FAILED")

    def rows(self, document, fence):
        dimension = self.store._validate_document(document)
        if (dimension != self.settings.rag_embedding_dimension
                or document.embedding_model_version != self.settings.rag_embedding_model
                or document.index_revision < 1 or document.activation_revision < 1):
            _fail()
        chunks = self.store._chunk_rows(document, dimension, active=True, fence=fence)
        return [*chunks, self.store._manifest_row(document, dimension, chunks, fence=fence)]

    def restore(self, identity, rows):
        """从完整 manifest 还原并逐字段复核；不从活动标记推断权威身份。"""
        matching = [row for row in rows if row.get("documentId") == identity.document_id
                    and row.get("documentVersion") == identity.document_version
                    and row.get("indexRevision") == identity.index_revision
                    and row.get("activationRevision") == identity.activation_revision]
        manifests = [row for row in matching if row.get("recordType") == "manifest"]
        if len(manifests) != 1:
            _fail("KNOWLEDGE_PUBLICATION_SOURCE_INCOMPLETE")
        manifest = manifests[0]
        if manifest.get("isActive") is not False:
            _fail()
        chunk_ids = manifest.get("chunkIds")
        if (not isinstance(chunk_ids, list) or not chunk_ids
                or len(chunk_ids) > 10_000 or len(chunk_ids) != len(set(chunk_ids))
                or manifest.get("expectedChunkCount") != len(chunk_ids)):
            _fail()
        by_id = {row["id"]: row for row in matching}
        if set(by_id) != {*chunk_ids, manifest["id"]} or len(by_id) != len(matching):
            _fail("KNOWLEDGE_PUBLICATION_SOURCE_INCOMPLETE")
        chunks = []
        for index, row_id in enumerate(chunk_ids):
            row = by_id[row_id]
            if (row.get("recordType") != "chunk" or row.get("isActive") is not True
                    or type(row.get("chunkIndex")) is not int or row["chunkIndex"] != index
                    or hashlib.sha256(row.get("content", "").encode()).hexdigest() != row.get("contentHash")):
                _fail()
            chunks.append(IndexedChunk(
                chunk_id=row["chunkId"], chunk_index=row["chunkIndex"], content=row["content"],
                source_locator=row["sourceLocator"], content_hash=row["contentHash"],
                embedding=tuple(self.store._canonical_vector(row["embedding"])),
            ))
        document = IndexedDocument(
            document_id=identity.document_id, document_version=identity.document_version,
            etl_version=str(identity.index_revision), embedding_model_version=self.settings.rag_embedding_model,
            file_name=identity.file_name, file_type=identity.file_type.lower(),
            content_hash=identity.sha256, chunks=tuple(chunks),
            index_revision=identity.index_revision, activation_revision=identity.activation_revision,
        )
        expected = self.rows(document, 1)
        if any(_encoded(_canonical(by_id[row["id"]])) != _encoded(_canonical(row)) for row in expected):
            _fail("KNOWLEDGE_VERSION_CONFLICT")
        return document

    async def completed(self, collection):
        metadata = await self.metadata(collection)
        rows = await self.scan(collection)
        markers = [row for row in rows if row.get("recordType") == "publication_completion"]
        if len(markers) != 1 or markers[0].get("id") != _COMPLETION_ID:
            _fail("KNOWLEDGE_PUBLICATION_INCOMPLETE")
        marker = markers[0]
        body = [row for row in rows if row is not marker]
        try:
            receipt = CandidateReceipt.model_validate(marker["receipt"])
        except Exception:
            _fail("KNOWLEDGE_PUBLICATION_INCOMPLETE")
        if (receipt.publication_id != metadata["publicationId"]
                or receipt.base_epoch != metadata["baseEpoch"]
                or receipt.operation_id != metadata["operationId"]
                or receipt.fence != metadata["mutationFence"]
                or receipt.candidate_collection != collection
                or receipt.embedding_model_version != metadata["embeddingModelVersion"]
                or receipt.embedding_dimension != metadata["embeddingDimension"]
                or receipt.snapshot_hash != snapshot_hash(body)
                or _canonical(marker) != _canonical(self.completion(receipt))):
            _fail("KNOWLEDGE_PUBLICATION_INCOMPLETE")
        if any(type(row.get("mutationFence")) is not int or row["mutationFence"] != receipt.fence for row in body):
            _fail("KNOWLEDGE_PUBLICATION_INCOMPLETE")
        expected_ids = set()
        for item in receipt.documents:
            identity = DocumentIdentity.model_validate(item.model_dump(exclude={"chunk_count"}))
            document = self.restore(identity, body)
            if len(document.chunks) != item.chunk_count:
                _fail()
            expected_ids.update(row["id"] for row in self.rows(document, 1))
        if expected_ids != {row["id"] for row in body}:
            _fail("KNOWLEDGE_PUBLICATION_SOURCE_INCOMPLETE")
        self.budget(rows, documents=len(receipt.documents))
        return receipt, body

    async def source(self, context: PublicationContext):
        collection = context.source_collection
        if collection is None:
            if context.base_epoch != 0:
                _fail()
            return []
        if self._name_valid(collection):
            if context.publication_id.startswith("bootstrap_"):
                _fail()
            _, rows = await self.completed(collection)
            return rows
        if not context.publication_id.startswith("bootstrap_"):
            _fail("KNOWLEDGE_PUBLICATION_SOURCE_INVALID")
        await self.store._validate_collection(
            collection, self.settings.rag_embedding_model, self.settings.rag_embedding_dimension,
        )
        metadata = self.store._metadata(await self.store._describe_collection(collection))
        if metadata.get("aliasHash") != self.store._alias_hash():
            _fail()
        # 旧集合可能有历史代际，逐文档读取当前预期身份，不把旧历史复制到新权威库。
        rows = []
        for item in context.documents:
            rows.extend(await self.store._document_rows(collection, item.document_id))
            self.budget(rows)
        return rows

    def preflight_receipt(self, context, lease):
        if not 2 <= self.settings.rag_embedding_dimension <= 65_536:
            _fail("KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH")
        # chunkCount 用上限位数预留，在首次付费 embedding 前拒绝 HTTP 不可承载的名单。
        value = self.receipt(context, lease, self.store._alias[:210] + "_pub_" + "0" * 32,
                             "0" * 64, [ReceiptDocument(**item.model_dump(), chunk_count=10_000)
                                          for item in context.documents])
        self.check_receipt(value)
        self.budget([self.completion(value)], documents=len(context.documents))

    @staticmethod
    def check_receipt(receipt):
        if len(receipt.model_dump_json(by_alias=True).encode()) > RECEIPT_MAX_BYTES:
            _fail("KNOWLEDGE_PUBLICATION_RECEIPT_TOO_LARGE")

    def receipt(self, context, lease, name, digest, documents):
        return CandidateReceipt(
            publication_id=context.publication_id, base_epoch=context.base_epoch,
            operation_id=lease.operation_id, fence=lease.fence, candidate_collection=name,
            snapshot_hash=digest, embedding_model_version=self.settings.rag_embedding_model,
            embedding_dimension=self.settings.rag_embedding_dimension, documents=documents,
        )

    def completion(self, receipt):
        return {"id": _COMPLETION_ID, "recordType": "publication_completion",
                "receipt": receipt.model_dump(by_alias=True),
                "schemaVersion": self.store._schema_version,
                "embedding": [0.0] * self.settings.rag_embedding_dimension}

    async def write(self, context, lease, documents, permit):
        if [document_identity(item) for item in documents] != context.documents:
            _fail()
        name = self.store._alias[:210] + "_pub_" + uuid4().hex
        if await self.store._call("has_collection", name):
            _fail("KNOWLEDGE_PUBLICATION_NAME_CONFLICT")
        rows = []
        for document in documents:
            rows.extend(self.rows(document, lease.fence))
            self.budget(rows, documents=len(documents))
        receipt = self.receipt(context, lease, name, snapshot_hash(rows), [
            ReceiptDocument(**document_identity(item).model_dump(), chunk_count=len(item.chunks))
            for item in documents])
        self.check_receipt(receipt)
        completion = self.completion(receipt)
        self.budget([*rows, completion], documents=len(documents))
        metadata = {"kind": KIND, "aliasHash": self.store._alias_hash(),
                    "schemaVersion": self.store._schema_version,
                    "embeddingModelVersion": receipt.embedding_model_version,
                    "embeddingDimension": receipt.embedding_dimension,
                    "publicationId": context.publication_id, "baseEpoch": context.base_epoch,
                    "operationId": lease.operation_id, "operation": lease.operation,
                    "mutationFence": lease.fence}
        await self.store._assert_permit(permit)
        await self.store._call(
            "create_collection", name, dimension=receipt.embedding_dimension,
            primary_field_name="id", id_type="string", vector_field_name="embedding",
            metric_type="COSINE", auto_id=False, max_length=64, enable_dynamic_field=True,
            consistency_level="Strong", description=_encoded(metadata).decode(),
        )
        await self.metadata(name)
        for start in range(0, len(rows), 100):
            await self.store._assert_permit(permit)
            await self.store._write_exact("insert", name, rows[start:start + 100])
        actual = await self.scan(name)
        if ({row["id"]: _encoded(_canonical(row)) for row in actual} != {row["id"]: _encoded(_canonical(row)) for row in rows}
                or any(row.get("mutationFence") != lease.fence
                       or row.get("isActive") is not (row.get("recordType") == "chunk") for row in actual)):
            _fail("KNOWLEDGE_PUBLICATION_INCOMPLETE")
        await self.store._assert_permit(permit)
        await self.store._write_exact("insert", name, [completion])
        verified, _ = await self.completed(name)
        if verified != receipt:
            _fail()
        await self.store._assert_permit(permit)
        return receipt

    async def validate_publication(self, snapshot: ReadSnapshot, *, verify_rows=False):
        if (not snapshot.enabled or snapshot.embedding_model_version != self.settings.rag_embedding_model
                or snapshot.embedding_dimension != self.settings.rag_embedding_dimension):
            _fail()
        if verify_rows:
            receipt, _ = await self.completed(snapshot.collection_name)
        else:
            metadata = await self.metadata(snapshot.collection_name)
            markers = await self.store._get_rows(snapshot.collection_name, [_COMPLETION_ID],
                output_fields=["id", "recordType", "receipt", "schemaVersion"])
            if len(markers) != 1:
                _fail("KNOWLEDGE_PUBLICATION_INCOMPLETE")
            try:
                receipt = CandidateReceipt.model_validate(markers[0]["receipt"])
            except Exception:
                _fail("KNOWLEDGE_PUBLICATION_INCOMPLETE")
            expected = self.completion(receipt)
            if (any(_encoded(markers[0].get(key)) != _encoded(expected[key])
                    for key in ("id", "recordType", "receipt", "schemaVersion"))
                    or receipt.candidate_collection != snapshot.collection_name
                    or receipt.publication_id != metadata["publicationId"]
                    or receipt.base_epoch != metadata["baseEpoch"]
                    or receipt.operation_id != metadata["operationId"]
                    or receipt.fence != metadata["mutationFence"]
                    or receipt.embedding_model_version != metadata["embeddingModelVersion"]
                    or receipt.embedding_dimension != metadata["embeddingDimension"]):
                _fail("KNOWLEDGE_PUBLICATION_INCOMPLETE")
            self.check_receipt(receipt)
        if (receipt.publication_id != snapshot.publication_id or receipt.snapshot_hash != snapshot.snapshot_hash
                or snapshot.epoch <= receipt.base_epoch):
            _fail()
        by_id = {item.document_id: item for item in receipt.documents}
        for item in snapshot.documents:
            original = by_id.get(item.document_id)
            if original is None or original.model_dump(exclude={"chunk_count"}) != item.model_dump():
                _fail()
        return receipt

    async def search_publication(self, vector, limit, snapshot: ReadSnapshot):
        if (type(limit) is not int or not 1 <= limit <= 100 or not isinstance(vector, list)
                or len(vector) != self.settings.rag_embedding_dimension
                or any(type(value) not in {int, float} or not math.isfinite(value) for value in vector)):
            _fail("KNOWLEDGE_SEARCH_INVALID")
        receipt = await self.validate_publication(snapshot)
        if not snapshot.documents:
            return []
        allowed = {item.document_id: item for item in snapshot.documents}
        counts = {item.document_id: item.chunk_count for item in receipt.documents}
        fields = ["id", "recordType", "chunkId", "documentId", "documentVersion", "chunkIndex",
                  "etlVersion", "embeddingModelVersion", "fileName", "fileType", "sourceLocator",
                  "content", "contentHash", "documentContentHash", "embeddingDimension",
                  "schemaVersion", "indexRevision", "activationRevision", "revisionProtocol"]
        result = await self.store._call(
            "search", snapshot.collection_name, data=[vector], limit=limit,
            filter='recordType == "chunk" and documentId in ' + json.dumps(sorted(allowed)),
            output_fields=fields, search_params={"metric_type": "COSINE", "params": {}},
            consistency_level="Strong",
        )
        hits = []
        manifests = {}
        for hit in result[0] if result else []:
            row = hit.get("entity", hit)
            record_id = hit.get("id", row.get("id"))
            identity = allowed.get(row.get("documentId"))
            if identity is None:
                _fail()
            document = IndexedDocument(
                identity.document_id, identity.document_version, str(identity.index_revision),
                self.settings.rag_embedding_model, identity.file_name, identity.file_type.lower(),
                identity.sha256, (), identity.index_revision, identity.activation_revision,
            )
            identity_key = self.store._identity(document)
            if identity.document_id not in manifests:
                manifest_id = hashlib.sha256(f"{identity_key}\0manifest".encode()).hexdigest()
                rows = await self.store._get_rows(snapshot.collection_name, [manifest_id],
                    output_fields=[*fields, "chunkIds", "expectedChunkCount"])
                if len(rows) != 1:
                    _fail("KNOWLEDGE_PUBLICATION_SOURCE_INCOMPLETE")
                manifest = rows[0]
                ids = manifest.get("chunkIds")
                if (not isinstance(ids, list) or len(ids) != counts[identity.document_id]
                        or len(ids) != len(set(ids))):
                    _fail()
                expected = self.store._manifest_row(document, self.settings.rag_embedding_dimension,
                    [{"id": value} for value in ids], fence=1)
                if any(_encoded(manifest.get(key)) != _encoded(value) for key, value in expected.items()
                       if key not in {"embedding", "mutationFence", "isActive"}):
                    _fail()
                manifests[identity.document_id] = ids
            ids = manifests[identity.document_id]
            index = row.get("chunkIndex")
            if (type(index) is not int or not 0 <= index < len(ids)
                    or not isinstance(row.get("chunkId"), str)
                    or record_id != hashlib.sha256(f"{identity_key}\0{row['chunkId']}".encode()).hexdigest()
                    or record_id != ids[index]
                    or not isinstance(row.get("content"), str)
                    or hashlib.sha256(row["content"].encode()).hexdigest() != row.get("contentHash")
                    or not isinstance(row.get("sourceLocator"), str)
                    or len(row["sourceLocator"]) > 2048
                    or len(row["content"]) > self.settings.rag_chunk_size):
                _fail()
            expected_fields = {
                "recordType": "chunk", "documentVersion": identity.document_version,
                "indexRevision": identity.index_revision, "activationRevision": identity.activation_revision,
                "documentContentHash": identity.sha256, "fileName": identity.file_name,
                "fileType": identity.file_type.lower(), "etlVersion": str(identity.index_revision),
                "embeddingModelVersion": self.settings.rag_embedding_model,
                "embeddingDimension": self.settings.rag_embedding_dimension,
                "schemaVersion": self.store._schema_version, "revisionProtocol": 2,
            }
            if any(_encoded(row.get(key)) != _encoded(expected) for key, expected in expected_fields.items()):
                _fail()
            score = float(hit.get("distance", hit.get("score")))
            if not math.isfinite(score):
                _fail()
            hits.append(RetrievedChunk(
                chunk_id=row["chunkId"], document_id=row["documentId"],
                document_version=row["documentVersion"], chunk_index=row["chunkIndex"],
                content=row["content"], file_name=row["fileName"], file_type=row["fileType"],
                source_locator=row["sourceLocator"], content_hash=row["contentHash"],
                distance=1.0 - score, score=score, current_document_version=row["documentVersion"],
                index_revision=row["indexRevision"], activation_revision=row["activationRevision"],
                document_content_hash=row["documentContentHash"],
            ))
        return hits
