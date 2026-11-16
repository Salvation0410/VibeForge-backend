"""权威发布候选准备：只返回经过完整验证的收据，提交由 Spring 负责。"""
from __future__ import annotations

import asyncio
from dataclasses import replace

from ai_service.api.knowledge_publication_schemas import CandidateRequest
from ai_service.infrastructure.knowledge_publication_store import PublicationStore, document_identity
from ai_service.infrastructure.milvus_knowledge import KnowledgeMutationLease, MilvusKnowledgeError
from ai_service.orchestration.document_etl import KnowledgeEtlService


class _AlreadyHeld:
    """外层已经持有同一个 ETL 信号量，复用原 ETL 编排时不得再次 acquire。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class _Capture:
    """截获原 ETL 的最终结构，禁止其进入旧 alias 写路径。"""

    def __init__(self):
        self.documents = []

    async def upsert_document_version(self, document, **_kwargs):
        self.documents.append(document)

    async def rebuild_collection(self, documents, **_kwargs):
        async for document in documents:
            self.documents.append(document)


class _EmbeddingBudget:
    """原 ETL 完成解析后、付费调用前，核对候选累计预算。"""

    def __init__(self, provider, publications, retained):
        self.provider, self.publications, self.retained = provider, publications, retained

    async def embed_documents(self, texts, *, max_elements):
        settings = self.publications.settings
        old = [row for row in self.retained if row.get("recordType") == "chunk"]
        if (len(old) + len(texts)) * settings.rag_embedding_dimension > settings.rag_max_embedding_elements:
            raise MilvusKnowledgeError("KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED")
        if len(old) + len(texts) > settings.rag_rebuild_max_chunks:
            raise MilvusKnowledgeError("KNOWLEDGE_REBUILD_TOO_MANY_CHUNKS")
        if sum(len(text.encode()) for text in texts) + sum(len(row["content"].encode()) for row in old) > settings.rag_rebuild_max_text_bytes:
            raise MilvusKnowledgeError("KNOWLEDGE_REBUILD_TEXT_BUDGET_EXCEEDED")
        return await self.provider.embed_documents(texts, max_elements=max_elements)


class KnowledgePublicationPreparer:
    def __init__(self, settings, downloader, embeddings, store, semaphore=None):
        self.settings = settings
        self.store = store
        self.publications = PublicationStore(settings, store)
        self.downloader, self.embeddings = downloader, embeddings
        self.semaphore = semaphore if semaphore is not None else asyncio.Semaphore(settings.rag_etl_max_concurrency)

    async def prepare(self, request: CandidateRequest):
        mutation, context = request.mutation, request.context
        lease = KnowledgeMutationLease(**mutation.lease.model_dump())
        operation = {"INDEX": "upsert", "DELETE": "delete", "REBUILD": "rebuild"}[mutation.operation]
        if mutation.operation == "REBUILD" and mutation.collection_alias != self.store._alias:
            raise MilvusKnowledgeError("KNOWLEDGE_COLLECTION_ALIAS_INVALID")
        if mutation.operation == "REBUILD" and not context.documents:
            raise MilvusKnowledgeError("KNOWLEDGE_EMPTY_REBUILD_BLOCKED")
        self.publications.preflight_receipt(context, lease)
        async with self.semaphore:
            async with self.store._mutation(lease, scope=lease.scope, operation=operation) as permit:
                source = await self.publications.source(context)
                documents = {}
                for item in context.documents:
                    if mutation.operation == "INDEX" and item.document_id == mutation.document_id:
                        continue
                    documents[item.document_id] = self.publications.restore(item, source)
                await self.store._assert_permit(permit)
                capture = _Capture()
                retained = [row for document in documents.values() for row in self.publications.rows(document, 1)]
                embeddings = _EmbeddingBudget(self.embeddings, self.publications, retained)
                etl = KnowledgeEtlService(self.settings, self.downloader, embeddings,
                                          capture, semaphore=_AlreadyHeld())
                if mutation.operation == "INDEX":
                    kwargs = mutation.model_dump(exclude={"operation", "lease"})
                    await etl.index(**kwargs, lease=lease)
                    document = capture.documents[0]
                    if document_identity(document) != next(
                            item for item in context.documents if item.document_id == mutation.document_id):
                        raise MilvusKnowledgeError("KNOWLEDGE_PUBLICATION_INVALID")
                    documents[document.document_id] = document
                elif mutation.operation == "REBUILD":
                    # 原 ETL 下载、校验原文并按同一设置切块；正代际路径不调用 Embedding。
                    await etl.rebuild(documents=mutation.documents, etl_version=mutation.etl_version, lease=lease)
                    for parsed in capture.documents:
                        original = documents[parsed.document_id]
                        restored = replace(parsed, chunks=tuple(
                            replace(chunk, embedding=original.chunks[index].embedding)
                            for index, chunk in enumerate(parsed.chunks)
                        )) if len(parsed.chunks) == len(original.chunks) else None
                        if restored is None or (
                            self.publications.rows(restored, 1) != self.publications.rows(original, 1)
                        ):
                            raise MilvusKnowledgeError("KNOWLEDGE_VERSION_CONFLICT")
                        documents[parsed.document_id] = restored
                await self.store._assert_permit(permit)
                return await self.publications.write(
                    context, lease, [documents[item.document_id] for item in context.documents], permit,
                )

    async def validate_publication(self, snapshot, *, verify_rows=False):
        return await self.publications.validate_publication(snapshot, verify_rows=verify_rows)

    async def search_publication(self, vector, limit, snapshot):
        return await self.publications.search_publication(vector, limit, snapshot)
