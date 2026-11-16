"""真实 Milvus 代际验收；合成向量和进程内许可不代表 Spring fence 验收。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from pymilvus import MilvusClient

from ai_service.infrastructure.milvus_knowledge import (
    IndexedChunk,
    IndexedDocument,
    KnowledgeMutationLease,
    MilvusKnowledgeError,
    MilvusKnowledgeStore,
    RebuildFingerprint,
    RebuildPlan,
)

pytestmark = pytest.mark.skipif(
    os.getenv("AI_SERVICE_MILVUS_INTEGRATION", "").lower() != "true",
    reason="需要显式启用 AI_SERVICE_MILVUS_INTEGRATION=true 和本地 Milvus",
)


class LocalCoordinator:
    """只串行化本测试进程，不模拟或声称跨实例的 Spring 可信租约。"""

    def __init__(self):
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def hold(self, lease, *, scope, operation):
        assert lease.scope == scope
        async with self.lock:
            class Permit:
                fence = lease.fence

                async def assert_current(self):
                    assert lease.expires_at > time.time()

            yield Permit()


class OwnedClient:
    """透传真实 RPC，只登记本例创建的名称，禁止越过随机 alias 的写入边界。"""

    def __init__(self, client, alias):
        self.client = client
        self.alias = alias
        self.created = set()

    def create_collection(self, collection_name, *args, **kwargs):
        assert collection_name.startswith(self.alias + "_")
        self.created.add(collection_name)
        return self.client.create_collection(collection_name, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.client, name)


@pytest.fixture
async def real_store(settings):
    # 不读取 .env 中的生产地址/alias，也不接触业务 MySQL 或付费 embedding。
    alias = "rag_revision_it_" + uuid4().hex
    settings.milvus_uri = "http://localhost:19530"
    settings.milvus_token = ""
    settings.milvus_database = "default"
    settings.milvus_collection_alias = alias
    settings.rag_embedding_model = "integration-synthetic-v1"
    settings.milvus_rpc_timeout_seconds = 20
    client = MilvusClient(uri=settings.milvus_uri, timeout=20)
    owned = OwnedClient(client, alias)
    knowledge = MilvusKnowledgeStore(
        settings, client_factory=lambda **_kwargs: owned,
        mutation_coordinator=LocalCoordinator(),
    )
    try:
        yield knowledge, client, alias
    finally:
        # 只清理本例实际尝试创建、且元数据再次证明归属的集合。
        # 不扫描或删除任何生产集合；清理失败必须让测试失败，不能伪装无残留。
        try:
            aliases = client.list_aliases(timeout=20)
            names = aliases.get("aliases", []) if isinstance(aliases, dict) else aliases
            if alias in names:
                target = client.describe_alias(alias, timeout=20)["collection_name"]
                assert target in owned.created
                client.drop_alias(alias, timeout=20)
            expected_hash = hashlib.sha256(alias.encode()).hexdigest()[:12]
            for name in sorted(owned.created):
                assert name.startswith(alias + "_")
                if client.has_collection(name, timeout=20):
                    metadata = json.loads(client.describe_collection(name, timeout=20)["description"])
                    assert metadata["aliasHash"] == expected_hash
                    assert metadata["kind"] in {
                        "customer-service-knowledge", "customer-service-knowledge-mutation-control",
                    }
                    client.drop_collection(name, timeout=20)
            assert not any(client.has_collection(name, timeout=20) for name in owned.created)
        finally:
            await knowledge.close()


def revision(index=1, activation=1):
    chunks = tuple(IndexedChunk(
        chunk_id=f"doc-1:1:{number}", chunk_index=number,
        content=f"合成知识正文-{number}", source_locator=f"section-{number}",
        content_hash=f"chunk-hash-{number}", embedding=vector,
    ) for number, vector in enumerate(((1.0, 0.0), (0.8, 0.2))))
    return IndexedDocument(
        document_id="doc-1", document_version=1, etl_version=str(index),
        embedding_model_version="integration-synthetic-v1", file_name="manual.txt",
        file_type="txt", content_hash="same-content-hash", chunks=chunks,
        index_revision=index, activation_revision=activation,
    )


def lease(alias, fence, operation="INDEX"):
    return KnowledgeMutationLease(
        scope=f"collection:{alias}" if operation == "REBUILD" else "document:doc-1",
        operation_id=f"integration-{fence}", operation=operation, fence=fence,
        expires_at=time.time() + 300, proof="local-integration-only",
    )


async def documents(*values):
    for value in values:
        yield value


def plan(alias, values, build="integration-build"):
    fingerprint = RebuildFingerprint(alias, build)
    for value in values:
        fingerprint.add_document(
            document_id=value.document_id, document_version=value.document_version,
            file_name=value.file_name, file_type=value.file_type, content_hash=value.content_hash,
            index_revision=value.index_revision, activation_revision=value.activation_revision,
        )
    return RebuildPlan("integration-synthetic-v1", 2, build, len(values), fingerprint.hexdigest())


async def assert_serving(knowledge):
    hits = await knowledge.search([1.0, 0.0], 8)
    assert len(hits) == 2
    assert {hit.current_document_version for hit in hits} == {1}


async def test_real_revision_lifecycle_and_rebuild(real_store):
    knowledge, client, alias = real_store
    first = await knowledge.upsert_document_version(revision(), lease=lease(alias, 1))
    assert not first.idempotent
    assert (await knowledge.upsert_document_version(revision(), lease=lease(alias, 2))).idempotent
    second = await knowledge.upsert_document_version(revision(2), lease=lease(alias, 3))
    assert not second.idempotent
    await assert_serving(knowledge)
    await knowledge.delete_document("doc-1", 1, activation_revision=1, lease=lease(alias, 4, "DELETE"))
    assert await knowledge.search([1.0, 0.0], 8) == []
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_DOCUMENT_DISABLED$"):
        await knowledge.upsert_document_version(revision(4), lease=lease(alias, 5))
    enabled = revision(4, 2)
    await knowledge.upsert_document_version(enabled, lease=lease(alias, 6))
    before = client.describe_alias(alias)["collection_name"]
    rebuilt = await knowledge.rebuild_collection(
        documents(enabled), plan=plan(alias, [enabled]), lease=lease(alias, 7, "REBUILD"),
    )
    assert rebuilt.chunk_count == 2 and rebuilt.document_count == 1
    assert client.describe_alias(alias)["collection_name"] != before
    await knowledge.upsert_document_version(revision(5, 2), lease=lease(alias, 8))
    await knowledge.delete_document("doc-1", 1, activation_revision=1, lease=lease(alias, 9, "DELETE"))
    await assert_serving(knowledge)
    active = client.query(rebuilt.collection_name,
        filter='recordType == "chunk" and isActive == true',
        output_fields=["indexRevision", "activationRevision", "documentVersion"], consistency_level="Strong")
    assert len(active) == 2
    assert {(row["indexRevision"], row["activationRevision"], row["documentVersion"])
            for row in active} == {(5, 2, 1)}


async def test_real_v2_rebuild_reuses_verified_vector_snapshot(real_store):
    knowledge, client, alias = real_store
    original = revision(2)
    indexed = await knowledge.upsert_document_version(original, lease=lease(alias, 1))
    before = client.query(indexed.collection_name, filter='recordType == "chunk"',
        output_fields=["id", "embedding"], consistency_level="Strong")
    parsed = replace(original, chunks=tuple(replace(chunk, embedding=()) for chunk in original.chunks))
    rebuilt = await knowledge.rebuild_collection(
        documents(parsed), plan=plan(alias, [original]), lease=lease(alias, 2, "REBUILD"),
        reuse_revision_vectors=True,
    )
    after = client.query(rebuilt.collection_name, filter='recordType == "chunk"',
        output_fields=["id", "embedding"], consistency_level="Strong")
    assert rebuilt.collection_name != indexed.collection_name
    assert {row["id"]: row["embedding"] for row in after} == {
        row["id"]: row["embedding"] for row in before
    }
    await assert_serving(knowledge)


async def test_real_empty_and_legacy_rebuild_preserve_alias(real_store):
    knowledge, client, alias = real_store
    await knowledge.upsert_document_version(revision(2), lease=lease(alias, 1))
    before = client.describe_alias(alias)["collection_name"]
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_EMPTY_REBUILD_BLOCKED$"):
        await knowledge.rebuild_collection(documents(), plan=plan(alias, []), lease=lease(alias, 2, "REBUILD"))
    legacy = replace(revision(), index_revision=0, activation_revision=0, etl_version="legacy")
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_REVISION_PROTOCOL_REQUIRED$"):
        await knowledge.rebuild_collection(documents(legacy), plan=plan(alias, [legacy], "legacy"),
            lease=lease(alias, 3, "REBUILD"))
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_REVISION_PROTOCOL_REQUIRED$"):
        await knowledge.delete_document("doc-1", 1, lease=lease(alias, 4, "DELETE"))
    assert client.describe_alias(alias)["collection_name"] == before
    await assert_serving(knowledge)


@pytest.mark.parametrize("field,value", [("content", "非法改写正文"), ("embedding", (0.2, 0.8))])
async def test_real_same_revision_is_immutable(real_store, field, value):
    knowledge, client, alias = real_store
    original = revision(2)
    await knowledge.upsert_document_version(original, lease=lease(alias, 1))
    before = client.describe_alias(alias)["collection_name"]
    changed = replace(original, chunks=(replace(original.chunks[0], **{field: value}), original.chunks[1]))
    # 声明哈希未变而实际 chunk 改变时，INDEX 在逐行验证处以不完整快照拒绝。
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_STAGING_INCOMPLETE$"):
        await knowledge.upsert_document_version(changed, lease=lease(alias, 2))
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_VERSION_CONFLICT$"):
        await knowledge.rebuild_collection(documents(changed), plan=plan(alias, [changed]),
            lease=lease(alias, 3, "REBUILD"))
    assert client.describe_alias(alias)["collection_name"] == before
    assert (await knowledge.upsert_document_version(original, lease=lease(alias, 4))).idempotent
    await assert_serving(knowledge)


async def test_real_model_mismatch_and_missing_manifest_fail_closed(real_store):
    knowledge, client, alias = real_store
    original = revision()
    await knowledge.upsert_document_version(original, lease=lease(alias, 1))
    knowledge._query_embedding_model = "another-model-same-dimension"
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_COLLECTION_MISMATCH$"):
        await knowledge.search([1.0, 0.0], 8)
    knowledge._query_embedding_model = original.embedding_model_version
    target = client.describe_alias(alias)["collection_name"]
    # 故障注入仅删除本例 owned 集合的 manifest，真实 chunk 保留以验证拒绝服务。
    client.delete(target, filter='recordType == "manifest"', timeout=20)
    assert await knowledge.search([1.0, 0.0], 8) == []
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_REBUILD_REVISION_UNVERIFIABLE$"):
        await knowledge.rebuild_collection(documents(original), plan=plan(alias, [original]),
            lease=lease(alias, 2, "REBUILD"))
    assert client.describe_alias(alias)["collection_name"] == target
    await knowledge.upsert_document_version(revision(2), lease=lease(alias, 3))
    await assert_serving(knowledge)
