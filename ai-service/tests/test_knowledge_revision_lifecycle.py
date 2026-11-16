"""按 Spring 的内容、索引和激活代际串联操作；不替代跨资源验收。"""
from dataclasses import replace

import pytest

from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError, RebuildFingerprint, RebuildPlan
from test_milvus_knowledge import FakeMilvusClient, document, documents, lease, store


def revision(index=1, activation=1, version=1):
    return replace(document(version=version, etl=str(index)), index_revision=index,
                   activation_revision=activation)


def plan(values, build="build-1"):
    fingerprint = RebuildFingerprint("customer_service_knowledge", build)
    for value in values:
        fingerprint.add_document(document_id=value.document_id,
            document_version=value.document_version, file_name=value.file_name,
            file_type=value.file_type, content_hash=value.content_hash,
            index_revision=value.index_revision, activation_revision=value.activation_revision)
    return RebuildPlan("embedding-v1", 2, build, len(values), fingerprint.hexdigest())


@pytest.mark.asyncio
async def test_reindex_disable_enable_rebuild_reindex_preserve_content_identity(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    try:
        first = revision()
        await knowledge.upsert_document_version(first, lease=lease("document:doc-1", 1))
        assert (await knowledge.upsert_document_version(first, lease=lease("document:doc-1", 2))).idempotent
        second = revision(index=2)
        await knowledge.upsert_document_version(second, lease=lease("document:doc-1", 3))
        await knowledge.delete_document("doc-1", 1, activation_revision=1,
            lease=lease("document:doc-1", 4, operation="DELETE"))
        assert await knowledge.search([1, 0], 8) == []
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_DOCUMENT_DISABLED"):
            await knowledge.upsert_document_version(revision(index=4), lease=lease("document:doc-1", 5))
        enabled = revision(index=4, activation=2)
        await knowledge.upsert_document_version(enabled, lease=lease("document:doc-1", 6))
        await knowledge.rebuild_collection(documents(enabled), plan=plan([enabled]),
            lease=lease("collection:customer_service_knowledge", 7, operation="REBUILD"))
        await knowledge.upsert_document_version(revision(index=5, activation=2),
            lease=lease("document:doc-1", 8))
        assert len(await knowledge.search([1, 0], 8)) == 2
        # 迟到的旧撤销任务只覆盖旧激活，不得误伤新启用的索引。
        await knowledge.delete_document("doc-1", 1, activation_revision=1,
            lease=lease("document:doc-1", 9, operation="DELETE"))
        assert len(await knowledge.search([1, 0], 8)) == 2
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_revision_identity_is_immutable_and_older_index_cannot_reactivate(settings):
    knowledge = store(settings, FakeMilvusClient())
    try:
        await knowledge.upsert_document_version(revision(index=2), lease=lease("document:doc-1", 1))
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_STALE_VERSION"):
            await knowledge.upsert_document_version(revision(), lease=lease("document:doc-1", 2))
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_VERSION_CONFLICT"):
            await knowledge.upsert_document_version(replace(revision(index=3), content_hash="other-content"),
                lease=lease("document:doc-1", 3))
        changed = replace(revision(index=2), chunks=(replace(revision(index=2).chunks[0], content="changed"),))
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_VERSION_CONFLICT"):
            await knowledge.upsert_document_version(changed, lease=lease("document:doc-1", 4))
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_empty_rebuild_cannot_publish_over_existing_active_chunks(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    try:
        await knowledge.upsert_document_version(revision(), lease=lease("document:doc-1", 1))
        before = dict(client.aliases)
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_EMPTY_REBUILD_BLOCKED"):
            await knowledge.rebuild_collection(documents(), plan=plan([]),
                lease=lease("collection:customer_service_knowledge", 2, operation="REBUILD"))
        assert client.aliases == before
        assert len(await knowledge.search([1, 0], 8)) == 2
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_v2_protocol_cannot_be_downgraded_by_rebuild_or_delete(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    try:
        await knowledge.upsert_document_version(revision(index=2), lease=lease("document:doc-1", 1))
        before = dict(client.aliases)
        legacy = document(etl="old-build")
        from test_milvus_knowledge import rebuild_fingerprint
        legacy_plan = RebuildPlan("embedding-v1", 2, "old-build", 1,
                                  rebuild_fingerprint([legacy], etl="old-build"))
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_REVISION_PROTOCOL_REQUIRED"):
            await knowledge.rebuild_collection(documents(legacy), plan=legacy_plan,
                lease=lease("collection:customer_service_knowledge", 2, operation="REBUILD"))
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_REVISION_PROTOCOL_REQUIRED"):
            await knowledge.delete_document("doc-1", 1, lease=lease("document:doc-1", 3, operation="DELETE"))
        assert client.aliases == before
        assert len(await knowledge.search([1, 0], 8)) == 2
    finally:
        await knowledge.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("content", "changed"), ("source_locator", "other-section"),
                                         ("embedding", (0.7, 0.3))])
async def test_rebuild_must_not_recompute_different_chunks_under_same_revision(settings, field, value):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    first = revision(index=2)
    try:
        await knowledge.upsert_document_version(first, lease=lease("document:doc-1", 1))
        before = dict(client.aliases)
        changed = replace(first, chunks=(replace(first.chunks[0], **{field: value}), first.chunks[1]))
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_VERSION_CONFLICT"):
            await knowledge.rebuild_collection(documents(changed), plan=plan([changed]),
                lease=lease("collection:customer_service_knowledge", 2, operation="REBUILD"))
        assert client.aliases == before
        assert (await knowledge.upsert_document_version(first, lease=lease("document:doc-1", 3))).idempotent
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_missing_prior_manifest_requires_new_index_before_rebuild(settings):
    knowledge = store(settings, FakeMilvusClient())
    try:
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_REBUILD_REVISION_UNVERIFIABLE"):
            await knowledge.rebuild_collection(documents(revision()), plan=plan([revision()]),
                lease=lease("collection:customer_service_knowledge", 1, operation="REBUILD"))
        await knowledge.upsert_document_version(revision(index=2), lease=lease("document:doc-1", 2))
        assert len(await knowledge.search([1, 0], 8)) == 2
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_failed_new_index_preserves_previous_serving_revision(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    try:
        await knowledge.upsert_document_version(revision(), lease=lease("document:doc-1", 1))
        client.corrupt_next_insert = True
        with pytest.raises(MilvusKnowledgeError):
            await knowledge.upsert_document_version(revision(index=2, version=2), lease=lease("document:doc-1", 2))
        assert {value.document_version for value in await knowledge.search([1, 0], 8)} == {1}
    finally:
        await knowledge.close()


@pytest.mark.asyncio
async def test_same_dimension_different_query_model_fails_before_search(settings):
    client = FakeMilvusClient()
    knowledge = store(settings, client)
    try:
        await knowledge.upsert_document_version(revision(), lease=lease("document:doc-1", 1))
        knowledge._query_embedding_model = "another-model-same-dimension"
        before = len(client.search_calls)
        with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_COLLECTION_MISMATCH"):
            await knowledge.search([1, 0], 8)
        assert len(client.search_calls) == before
    finally:
        await knowledge.close()


def test_http_v2_index_delete_propagate_revision_identity(app_factory, auth_headers, settings):
    from fastapi.testclient import TestClient
    from test_api import FakeKnowledgeEtlService, knowledge_etl_payload, knowledge_lease

    settings.customer_service_rag_enabled = True
    service = FakeKnowledgeEtlService()
    payload = knowledge_etl_payload()
    payload.update(indexRevision=4, activationRevision=2, etlVersion="4")
    with TestClient(app_factory(knowledge_etl_service=service)) as client:
        indexed = client.post("/internal/v1/customer-service/knowledge:etl", json=payload, headers=auth_headers)
        deleted = client.post("/internal/v1/customer-service/knowledge:delete", headers=auth_headers,
            json={"operation": "DELETE", "documentId": "doc-1", "documentVersion": 2,
                  "activationRevision": 2, "lease": knowledge_lease("delete-op", operation="DELETE")})
    assert indexed.status_code == 200
    assert indexed.json()["indexRevision"] == 4
    assert indexed.json()["activationRevision"] == 2
    assert service.index_calls[0]["index_revision"] == 4
    assert service.index_calls[0]["activation_revision"] == 2
    assert deleted.status_code == 200
    assert deleted.json()["activationRevision"] == 2
    assert service.delete_calls[0]["activation_revision"] == 2


@pytest.mark.parametrize("updates", [{"indexRevision": 2}, {"activationRevision": 1},
    {"indexRevision": True, "activationRevision": 1}, {"indexRevision": -1, "activationRevision": 1},
    {"indexRevision": 2, "activationRevision": 1, "etlVersion": "3"}])
def test_http_rejects_partial_or_invalid_revision_before_etl(app_factory, auth_headers, settings, updates):
    from fastapi.testclient import TestClient
    from test_api import FakeKnowledgeEtlService, knowledge_etl_payload

    settings.customer_service_rag_enabled = True
    service = FakeKnowledgeEtlService()
    payload = knowledge_etl_payload()
    payload.update(updates)
    with TestClient(app_factory(knowledge_etl_service=service)) as client:
        result = client.post("/internal/v1/customer-service/knowledge:etl", json=payload, headers=auth_headers)
    assert result.status_code == 422
    assert not service.index_calls


def test_http_etl_store_lifecycle_uses_spring_revision_contract(app_factory, auth_headers, settings, tmp_path):
    """使用实际路由、解析和存储函数串联，只有下载/向量供应商及 Milvus RPC 被替身替代。"""
    import hashlib
    import time
    from fastapi.testclient import TestClient
    from ai_service.orchestration.document_etl import KnowledgeEtlService

    path = tmp_path / "manual.txt"
    path.write_text("可公开的知识正文", encoding="utf8")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()

    class Download:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        @property
        def path(self):
            return path

    class Downloader:
        async def download(self, *_args, **_kwargs):
            return Download()

    class Embeddings:
        async def embed_documents(self, texts, **_kwargs):
            return [[1.0, 0.0] for _ in texts]

    rpc = FakeMilvusClient()
    knowledge = store(settings, rpc)
    settings.rag_embedding_dimension = 2
    settings.customer_service_rag_enabled = True
    etl = KnowledgeEtlService(settings, Downloader(), Embeddings(), knowledge)

    def permit(operation, fence):
        return {"scope": "collection:customer_service_knowledge" if operation == "REBUILD" else "document:doc-1",
                "operationId": f"task-{fence}", "operation": operation, "fence": fence,
                "expiresAt": time.time() + 300, "proof": "valid-proof"}

    def source(index, activation):
        return {"documentId": "doc-1", "documentVersion": 1, "fileName": "manual.txt", "fileType": "TXT",
                "signedUrl": "https://oss.example.test/manual", "sha256": sha,
                "indexRevision": index, "activationRevision": activation}

    with TestClient(app_factory(knowledge_etl_service=etl)) as http:
        def index(index, activation, fence):
            body = {**source(index, activation), "operation": "INDEX", "etlVersion": str(index),
                    "lease": permit("INDEX", fence)}
            response = http.post("/internal/v1/customer-service/knowledge:etl", json=body, headers=auth_headers)
            assert response.status_code == 200, response.json()
            assert response.json()["indexRevision"] == index

        index(1, 1, 1)
        index(2, 1, 2)
        deleted = http.post("/internal/v1/customer-service/knowledge:delete", headers=auth_headers,
            json={"operation": "DELETE", "documentId": "doc-1", "documentVersion": 1,
                  "activationRevision": 1, "lease": permit("DELETE", 3)})
        assert deleted.status_code == 200
        index(4, 2, 4)
        rebuilt = http.post("/internal/v1/customer-service/knowledge:rebuild", headers=auth_headers,
            json={"operation": "REBUILD", "collectionAlias": "customer_service_knowledge",
                  "documents": [source(4, 2)], "etlVersion": "build-1000", "lease": permit("REBUILD", 5)})
        assert rebuilt.status_code == 200, rebuilt.json()
        index(5, 2, 6)
        rows = rpc.collections[rpc.aliases["customer_service_knowledge"]]
        active = [row for row in rows if row.get("isActive")]
        assert active and {(row["indexRevision"], row["activationRevision"], row["documentVersion"])
                              for row in active} == {(5, 2, 1)}
