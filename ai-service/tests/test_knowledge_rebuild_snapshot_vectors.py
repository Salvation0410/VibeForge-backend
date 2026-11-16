"""v2 重建复用已验收向量；供应商漂移不得触发重算或放宽快照比较。"""
import hashlib

import pytest

from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError
from ai_service.orchestration.document_etl import KnowledgeEtlService
from test_milvus_knowledge import FakeMilvusClient, lease, store


@pytest.fixture
async def snapshot_etl(settings, tmp_path):
    path = tmp_path / "manual.txt"
    path.write_text("已验收原文", encoding="utf8")

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

    class DriftingEmbeddings:
        calls = 0

        async def embed_documents(self, texts, **_kwargs):
            self.calls += 1
            # 若 v2 REBUILD 意外再次调用，将返回不同向量并破坏严格逐值一致。
            vector = [0.1, 0.2] if self.calls == 1 else [0.7, 0.3]
            return [vector for _ in texts]

    client = FakeMilvusClient()
    knowledge = store(settings, client)
    embeddings = DriftingEmbeddings()
    service = KnowledgeEtlService(settings, Downloader(), embeddings, knowledge)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    source = {"documentId": "doc-1", "documentVersion": 1, "fileName": "manual.txt",
              "fileType": "TXT", "signedUrl": "https://oss.test/manual", "sha256": sha,
              "indexRevision": 5, "activationRevision": 1}
    await service.index(document_id="doc-1", document_version=1, file_name="manual.txt",
        file_type="TXT", signed_url=source["signedUrl"], sha256=sha, etl_version="5",
        index_revision=5, activation_revision=1, lease=lease("document:doc-1", 1))
    try:
        yield service, knowledge, client, embeddings, source
    finally:
        await knowledge.close()


async def test_v2_rebuild_uses_exact_stored_vectors_without_provider(snapshot_etl):
    service, knowledge, client, embeddings, source = snapshot_etl
    old = client.aliases["customer_service_knowledge"]
    original = [r for r in client.collections[old] if r.get("recordType") == "chunk"]
    result = await service.rebuild(documents=[source], etl_version="build-1",
        lease=lease("collection:customer_service_knowledge", 2, operation="REBUILD"))
    assert embeddings.calls == 1
    rebuilt = [r for r in client.collections[result.collection_name] if r.get("recordType") == "chunk"]
    assert [r["embedding"] for r in rebuilt] == [r["embedding"] for r in original]
    assert len(await knowledge.search([1, 0], 8)) == 1


@pytest.mark.parametrize("damage,code", [
    ("manifest", "KNOWLEDGE_REBUILD_REVISION_UNVERIFIABLE"),
    ("chunk", "KNOWLEDGE_REBUILD_REVISION_UNVERIFIABLE"),
    ("content", "KNOWLEDGE_VERSION_CONFLICT"),
    ("sourceLocator", "KNOWLEDGE_VERSION_CONFLICT"),
    ("contentHash", "KNOWLEDGE_VERSION_CONFLICT"),
    ("manifestChunkIds", "KNOWLEDGE_VERSION_CONFLICT"),
    ("manifestCount", "KNOWLEDGE_VERSION_CONFLICT"),
    ("vectorDimension", "KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH"),
    ("vectorNonFinite", "KNOWLEDGE_REBUILD_REVISION_UNVERIFIABLE"),
    ("model", "KNOWLEDGE_COLLECTION_MISMATCH"),
    ("dimension", "KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH"),
])
async def test_v2_rebuild_snapshot_rejects_missing_or_changed_data(snapshot_etl, settings, damage, code):
    service, _knowledge, client, embeddings, source = snapshot_etl
    old = client.aliases["customer_service_knowledge"]
    rows = client.collections[old]
    if damage in {"manifest", "chunk"}:
        client.collections[old] = [row for row in rows if row.get("recordType") != damage]
    elif damage == "model":
        settings.rag_embedding_model = "different-model"
    elif damage == "dimension":
        settings.rag_embedding_dimension = 3
    elif damage in {"manifestChunkIds", "manifestCount"}:
        manifest = next(row for row in rows if row.get("recordType") == "manifest")
        manifest["chunkIds" if damage == "manifestChunkIds" else "expectedChunkCount"] = [] if damage == "manifestChunkIds" else 7
    elif damage in {"vectorDimension", "vectorNonFinite"}:
        row = next(row for row in rows if row.get("recordType") == "chunk")
        row["embedding"] = [0.1] if damage == "vectorDimension" else [float("nan"), 0.2]
    else:
        row = next(row for row in rows if row.get("recordType") == "chunk")
        row[damage] = "被篡改的数据"
    with pytest.raises(MilvusKnowledgeError, match=f"^{code}$"):
        await service.rebuild(documents=[source], etl_version="build-1",
            lease=lease("collection:customer_service_knowledge", 2, operation="REBUILD"))
    assert embeddings.calls == 1
    assert client.aliases["customer_service_knowledge"] == old


async def test_v2_rebuild_rechecks_snapshot_after_restoration(snapshot_etl, monkeypatch):
    service, knowledge, client, embeddings, source = snapshot_etl
    old = client.aliases["customer_service_knowledge"]
    restore = knowledge._restore_rebuild_revision_vectors

    async def mutate_after_read(*args):
        document = await restore(*args)
        row = next(row for row in client.collections[old] if row.get("recordType") == "chunk")
        row["embedding"] = [0.9, 0.1]
        return document

    monkeypatch.setattr(knowledge, "_restore_rebuild_revision_vectors", mutate_after_read)
    with pytest.raises(MilvusKnowledgeError, match="^KNOWLEDGE_VERSION_CONFLICT$"):
        await service.rebuild(documents=[source], etl_version="build-1",
            lease=lease("collection:customer_service_knowledge", 2, operation="REBUILD"))
    assert embeddings.calls == 1
    assert client.aliases["customer_service_knowledge"] == old
