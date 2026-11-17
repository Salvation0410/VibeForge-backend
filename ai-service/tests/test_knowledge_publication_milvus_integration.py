"""随机隔离 Milvus 私有候选验收；合成向量，不调用付费模型。"""
from __future__ import annotations

import hashlib
import json
import os
from uuid import uuid4

import pytest
from pymilvus import MilvusClient

from ai_service.infrastructure.knowledge_publication_store import KIND, PublicationStore
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError, MilvusKnowledgeStore
from ai_service.orchestration.knowledge_publication import KnowledgePublicationPreparer
from test_knowledge_revision_milvus_integration import LocalCoordinator, OwnedClient
from test_knowledge_publication import (
    Downloader, Embeddings, index_request, rebuild_request, request, seed, snapshot, value,
)

pytestmark = pytest.mark.skipif(
    os.getenv("AI_SERVICE_MILVUS_INTEGRATION", "").lower() != "true",
    reason="需要显式启用 AI_SERVICE_MILVUS_INTEGRATION=true 和本地 Milvus",
)


@pytest.fixture
async def publication_env(settings):
    # 不读取 .env 地址、凭据或生产 alias；所有写入必须属于本例随机命名空间。
    alias = "rag_pub_it_" + uuid4().hex
    settings.milvus_uri = "http://localhost:19530"
    settings.milvus_token = ""
    settings.milvus_database = "default"
    settings.milvus_collection_alias = alias
    settings.rag_embedding_model = "publication-synthetic-v1"
    settings.rag_embedding_dimension = 2
    settings.milvus_rpc_timeout_seconds = 20
    client = MilvusClient(uri=settings.milvus_uri, timeout=20)
    owned = OwnedClient(client, alias)
    knowledge = MilvusKnowledgeStore(settings, client_factory=lambda **_kwargs: owned,
                                    mutation_coordinator=LocalCoordinator())
    try:
        yield settings, client, knowledge, PublicationStore(settings, knowledge)
    finally:
        try:
            aliases = client.list_aliases(timeout=20)
            if alias in aliases.get("aliases", []):
                target = client.describe_alias(alias, timeout=20)["collection_name"]
                assert target in owned.created
                client.drop_alias(alias, timeout=20)
            expected_hash = hashlib.sha256(alias.encode()).hexdigest()[:12]
            for name in sorted(owned.created):
                assert name.startswith(alias + "_")
                if client.has_collection(name, timeout=20):
                    metadata = json.loads(client.describe_collection(name, timeout=20)["description"])
                    assert metadata["aliasHash"] == expected_hash
                    assert metadata["kind"] in {KIND, "customer-service-knowledge", "customer-service-knowledge-mutation-control"}
                    client.drop_collection(name, timeout=20)
            assert not any(client.has_collection(name, timeout=20) for name in owned.created)
        finally:
            await knowledge.close()


async def test_real_private_index_rebuild_delete_and_scalar_search(publication_env, tmp_path):
    settings, client, knowledge, publications = publication_env
    path = tmp_path / "manual.txt"
    path.write_text("私有知识正文", encoding="utf-8")
    embeddings = Embeddings()
    preparer = KnowledgePublicationPreparer(settings, Downloader(path), embeddings, knowledge)
    document = value(settings)
    first = await preparer.prepare(index_request(settings, document))
    client.create_alias(first.candidate_collection, knowledge._alias)
    before = await publications.scan(first.candidate_collection)
    rebuilt = await preparer.prepare(rebuild_request(settings, first, document))
    assert embeddings.calls == 1 and first.snapshot_hash == rebuilt.snapshot_hash
    assert first.candidate_collection != rebuilt.candidate_collection
    hits = await publications.search_publication([0.1, 0.9], 8, snapshot(rebuilt))
    assert len(hits) == 1 and hits[0].index_revision == 1
    first_delete = await preparer.prepare(request(rebuilt))
    second_delete = await preparer.prepare(request(rebuilt))
    assert first_delete.candidate_collection != second_delete.candidate_collection
    assert not first_delete.documents
    assert not (await publications.validate_publication(snapshot(first_delete), verify_rows=True)).documents
    assert client.describe_alias(knowledge._alias)["collection_name"] == first.candidate_collection
    assert await publications.scan(first.candidate_collection) == before
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_COLLECTION_MISMATCH"):
        from test_milvus_knowledge import lease
        await knowledge.delete_document("doc-1", 1, activation_revision=1,
            lease=lease("document:doc-1", 3, operation="DELETE"))


async def test_real_missing_row_or_completion_cannot_validate(publication_env):
    settings, client, knowledge, publications = publication_env
    receipt = await seed(publication_env)
    body = await publications.scan(receipt.candidate_collection)
    chunk_id = next(row["id"] for row in body if row["recordType"] == "chunk")
    client.delete(receipt.candidate_collection, ids=[chunk_id], timeout=20)
    with pytest.raises(MilvusKnowledgeError):
        await publications.validate_publication(snapshot(receipt), verify_rows=True)
    # 已损坏源不能作为后续 prepare 的基线，即使该操作准备删除损坏文档。
    with pytest.raises(MilvusKnowledgeError):
        await KnowledgePublicationPreparer(settings, None, None, knowledge).prepare(request(receipt))


async def test_late_alias_write_from_separate_process_cannot_change_authoritative_search(publication_env):
    import subprocess
    import sys
    from ai_service.api.knowledge_publication_schemas import ReconcileRequest
    from ai_service.orchestration.knowledge_publication_boundary import reconcile_alias
    from test_authoritative_knowledge import Control
    settings, client, knowledge, publications = publication_env
    first = await seed(publication_env)
    second = await KnowledgePublicationPreparer(settings,None,None,knowledge).prepare(request(first))
    control = Control(snapshot(second))
    body = ReconcileRequest(collection_alias=knowledge._alias, collection_name=second.candidate_collection,
                            publication_id=second.publication_id,epoch=control.current.epoch)
    assert (await reconcile_alias(body,control,publications,settings)).reconciled
    # 新进程模拟已经暂停的旧镜像写入；只接受本例随机命名空间。
    script = """
import sys
from pymilvus import MilvusClient
alias,target=sys.argv[1:]
assert alias.startswith('rag_pub_it_') and target.startswith(alias+'_pub_')
c=MilvusClient(uri='http://localhost:19530',timeout=15)
c.alter_alias(target,alias,timeout=15);c.close()
"""
    subprocess.run([sys.executable,'-c',script,knowledge._alias,first.candidate_collection],
                   check=True,timeout=30,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                   creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
    assert client.describe_alias(knowledge._alias)['collection_name']==first.candidate_collection
    # 具体空候选仍为空，不受旧alias恢复影响；对账随后按当前head重新收敛。
    assert not await publications.search_publication([0.1,0.9],8,control.current)
    assert (await reconcile_alias(body,control,publications,settings)).reconciled
    assert client.describe_alias(knowledge._alias)['collection_name']==second.candidate_collection


async def test_real_missing_head_and_empty_whitelist_avoid_paid_calls(publication_env):
    from ai_service.infrastructure.authoritative_knowledge import AuthoritativeKnowledgeStore
    from ai_service.orchestration.customer_service_rag import CustomerServiceRagService
    from ai_service.models.base import CustomerServiceModelAnswer
    from test_authoritative_knowledge import Control
    from test_customer_service_rag import FakeEmbeddings, FakeAnswerModel, FakeReranker, FakeStore
    settings,client,knowledge,publications=publication_env
    settings.rag_publication_enabled=True
    receipt=await seed(publication_env);control=Control(snapshot(receipt))
    embeddings=FakeEmbeddings();model=FakeAnswerModel(CustomerServiceModelAnswer(True,'隔离答案',()))
    wrapper=AuthoritativeKnowledgeStore(settings,FakeStore(),publications,control)
    service=CustomerServiceRagService(settings,embeddings,wrapper,FakeReranker(),model)
    control.current.documents=[]
    result=await service.answer('隔离问题')
    assert not result.answered and not result.sources and not embeddings.questions and not model.calls
    client.drop_collection(receipt.candidate_collection,timeout=20)
    result=await service.answer('隔离问题')
    assert not result.answered and not result.sources and not embeddings.questions and not model.calls
