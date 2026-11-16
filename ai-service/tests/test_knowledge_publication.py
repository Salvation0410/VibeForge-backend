"""私有候选隔离、完整性及预算测试，不调用真实供应商。"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_service.api.knowledge_publication_schemas import (
    CandidateReceipt, CandidateRequest, PublicationContext, PublicationFact, ReadSnapshot,
    ReconcileRequest, ReconcileResponse,
)
from ai_service.infrastructure.knowledge_publication_store import PublicationStore, document_identity
from ai_service.infrastructure.milvus_knowledge import IndexedChunk, IndexedDocument, MilvusKnowledgeError
from ai_service.orchestration.knowledge_publication import KnowledgePublicationPreparer
from test_milvus_knowledge import FakeMilvusClient, lease, store


def value(settings, document_id="doc-1", content="私有知识正文"):
    return IndexedDocument(
        document_id=document_id, document_version=1, etl_version="1",
        embedding_model_version=settings.rag_embedding_model, file_name="manual.txt", file_type="txt",
        content_hash=hashlib.sha256(content.encode()).hexdigest(), index_revision=1, activation_revision=1,
        chunks=(IndexedChunk(f"{document_id}:1:0", 0, content, "lines 1-1",
                             hashlib.sha256(content.encode()).hexdigest(), (0.1, 0.9)),),
    )


@pytest.fixture
def env(settings):
    settings.rag_embedding_dimension = 2
    client = FakeMilvusClient()
    original_matches = client._matches
    def matches(row, expression):
        if " and documentId in " in expression:
            prefix, raw = expression.split(" and documentId in ")
            return original_matches(row, prefix) and row.get("documentId") in json.loads(raw)
        return original_matches(row, expression)
    client._matches = matches
    knowledge = store(settings, client)
    return settings, client, knowledge, PublicationStore(settings, knowledge)


async def seed(env, values=None):
    settings, client, knowledge, publications = env
    values = [value(settings)] if values is None else values
    context = PublicationContext(publication_id="bootstrap_" + "a" * 32, base_epoch=0,
                                 source_collection=None, documents=[document_identity(item) for item in values])
    permit_lease = lease("collection:" + knowledge._alias, operation="REBUILD")
    async with knowledge._mutation(permit_lease, scope=permit_lease.scope, operation="rebuild") as permit:
        return await publications.write(context, permit_lease, values, permit)


def request(receipt, operation="DELETE", documents=None):
    documents = [] if documents is None else documents
    mutation = {"operation": operation, "documentId": "doc-1", "documentVersion": 1,
                "activationRevision": 1, "lease": {
                    "scope": "document:doc-1", "operationId": "attempt_2", "operation": operation,
                    "fence": 2, "expiresAt": time.time() + 600, "proof": "valid-proof"}}
    return CandidateRequest.model_validate({
        "context": {"publicationId": "task_1", "baseEpoch": 1,
                    "sourceCollection": receipt.candidate_collection,
                    "documents": [item.model_dump(by_alias=True) for item in documents]},
        "mutation": mutation,
    })


def snapshot(receipt, documents=None):
    return ReadSnapshot(
        enabled=True, epoch=receipt.base_epoch + 1, publication_id=receipt.publication_id,
        collection_name=receipt.candidate_collection, snapshot_hash=receipt.snapshot_hash,
        embedding_model_version=receipt.embedding_model_version,
        embedding_dimension=receipt.embedding_dimension,
        documents=[document_identity_dummy(item) for item in receipt.documents] if documents is None else documents,
    )


def document_identity_dummy(item):
    from ai_service.api.knowledge_publication_schemas import DocumentIdentity
    return DocumentIdentity.model_validate(item.model_dump(exclude={"chunk_count"}))


async def test_candidates_are_distinct_and_never_change_source_or_alias(env):
    settings, client, knowledge, publications = env
    original = await seed(env)
    client.aliases[knowledge._alias] = original.candidate_collection
    before = deepcopy(client.collections)
    preparer = KnowledgePublicationPreparer(settings, None, None, knowledge)
    first = await preparer.prepare(request(original))
    second = await preparer.prepare(request(original))
    assert first.candidate_collection != second.candidate_collection
    assert first.snapshot_hash == second.snapshot_hash
    assert first.documents == second.documents == []
    assert client.collections[original.candidate_collection] == before[original.candidate_collection]
    assert client.aliases[knowledge._alias] == original.candidate_collection
    assert (await publications.validate_publication(snapshot(first))).documents == []


@pytest.mark.parametrize("corruption", ["missing", "extra", "duplicate", "content", "vector", "manifest", "hash", "model", "completion", "active", "fence"])
async def test_completed_source_corruption_fails_closed(env, corruption):
    settings, client, knowledge, publications = env
    receipt = await seed(env)
    rows = client.collections[receipt.candidate_collection]
    if corruption == "missing":
        rows.pop(0)
    elif corruption in {"extra", "duplicate"}:
        row = deepcopy(rows[0])
        if corruption == "extra":
            row["id"] = "f" * 64
        rows.append(row)
    elif corruption == "content":
        rows[0]["content"] = "损坏正文"
    elif corruption == "vector":
        rows[0]["embedding"][0] += 0.01
    elif corruption == "manifest":
        rows[1]["chunkIds"] = []
    elif corruption == "hash":
        rows[-1]["receipt"]["snapshotHash"] = "f" * 64
    elif corruption == "completion":
        rows.pop()
    elif corruption == "active":
        rows[0]["isActive"] = False
    elif corruption == "fence":
        rows[0]["mutationFence"] += 1
    else:
        metadata = json.loads(client.descriptions[receipt.candidate_collection])
        metadata["embeddingModelVersion"] = "another-model"
        client.descriptions[receipt.candidate_collection] = json.dumps(metadata)
    with pytest.raises(MilvusKnowledgeError):
        await publications.validate_publication(snapshot(receipt), verify_rows=True)


async def test_snapshot_whitelist_and_no_alias_tombstone_reads(env):
    settings, client, knowledge, publications = env
    receipt = await seed(env)
    client.fail_alias_lookup = True
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("不应读取共享墓碑")
    knowledge._tombstoned_versions = forbidden
    knowledge._revoked_activation = forbidden
    before = len(client.iterator_calls)
    hits = await publications.search_publication([0.1, 0.9], 8, snapshot(receipt))
    assert len(client.iterator_calls) == before
    assert all("embedding" not in call["output_fields"] for call in client.search_calls)
    assert all("embedding" not in fields and "*" not in fields for fields in client.get_output_fields)
    assert len(hits) == 1 and hits[0].index_revision == hits[0].activation_revision == 1
    assert hits[0].document_content_hash == receipt.documents[0].sha256
    assert await publications.search_publication([0.1, 0.9], 8, snapshot(receipt, [])) == []


async def test_old_delete_rejects_publication_target_without_mutating_it(env):
    _, client, knowledge, _ = env
    receipt = await seed(env)
    client.aliases[knowledge._alias] = receipt.candidate_collection
    before = deepcopy(client.collections[receipt.candidate_collection])
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_COLLECTION_MISMATCH"):
        await knowledge.delete_document("doc-1", 1, activation_revision=1,
            lease=lease("document:doc-1", 2, operation="DELETE"))
    assert client.collections[receipt.candidate_collection] == before


async def test_small_budget_rejected_before_embedding_or_collection_create(env):
    settings, client, knowledge, _ = env
    receipt = await seed(env)
    settings.rag_publication_max_bytes = 1
    before = set(client.collections)
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_PUBLICATION_BUDGET_EXCEEDED"):
        await KnowledgePublicationPreparer(settings, None, None, knowledge).prepare(request(receipt))
    assert set(client.collections) == before


async def test_failed_candidate_is_retained_without_completion(env):
    _, client, _, publications = env
    receipt = await seed(env)
    original = publications.scan
    async def corrupt(collection, **kwargs):
        result = await original(collection, **kwargs)
        if collection != receipt.candidate_collection:
            result[0]["embedding"][0] += 1
        return result
    publications.scan = corrupt
    before = set(client.collections)
    with pytest.raises(MilvusKnowledgeError):
        await seed((env[0], client, env[2], publications))
    created = set(client.collections) - before
    assert len(created) == 1
    assert all(row["recordType"] != "publication_completion" for row in client.collections[created.pop()])


def test_java_protocol_examples_and_strict_types():
    sample = json.loads((Path(__file__).parent / "fixtures/knowledge/publication-protocol.json").read_text())
    # 示例中的租约时间可能在将来测试时过期，类型与业务协议仍使用原样字段。
    sample["candidateRequest"]["mutation"]["lease"]["expiresAt"] = time.time() + 600
    CandidateRequest.model_validate(sample["candidateRequest"])
    CandidateReceipt.model_validate(sample["candidateReceipt"])
    PublicationFact.model_validate(sample["publicationFact"]["data"])
    ReadSnapshot.model_validate(sample["readSnapshot"]["data"])
    ReconcileRequest.model_validate(sample["reconcileRequest"])
    ReconcileResponse.model_validate(sample["reconcileResponse"])
    for bad in ["3", True, 0]:
        changed = deepcopy(sample["candidateRequest"])
        changed["mutation"]["indexRevision"] = bad
        with pytest.raises(ValidationError):
            CandidateRequest.model_validate(changed)


class Downloader:
    def __init__(self, path):
        self.path = path
        self.calls = 0

    async def download(self, url, *, expected_sha256, max_bytes):
        self.calls += 1
        assert hashlib.sha256(self.path.read_bytes()).hexdigest() == expected_sha256
        owner = self
        class File:
            path = owner.path
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None
        return File()


class Embeddings:
    def __init__(self):
        self.calls = 0

    async def embed_documents(self, texts, **_kwargs):
        self.calls += 1
        return [[0.1, 0.9] for _ in texts]


def index_request(settings, document):
    item = document_identity(document)
    return CandidateRequest.model_validate({
        "context": {"publicationId": "task_1", "baseEpoch": 0, "sourceCollection": None,
                    "documents": [item.model_dump(by_alias=True)]},
        "mutation": {"operation": "INDEX", **item.model_dump(by_alias=True),
                     "signedUrl": "https://example.test/download", "etlVersion": "1",
                     "lease": {"scope": "document:doc-1", "operationId": "attempt_1",
                               "operation": "INDEX", "fence": 1, "expiresAt": time.time() + 600,
                               "proof": "valid-proof"}},
    })


def rebuild_request(settings, receipt, document, *, bootstrap=False):
    item = document_identity(document)
    return CandidateRequest.model_validate({
        "context": {"publicationId": "bootstrap_" + "b" * 32 if bootstrap else "task_2",
                    "baseEpoch": 0 if bootstrap else 1,
                    "sourceCollection": receipt.candidate_collection,
                    "documents": [item.model_dump(by_alias=True)]},
        "mutation": {"operation": "REBUILD", "collectionAlias": settings.milvus_collection_alias,
                     "etlVersion": "rebuild-1", "documents": [{**item.model_dump(by_alias=True),
                         "signedUrl": "https://example.test/download"}],
                     "lease": {"scope": "collection:" + settings.milvus_collection_alias,
                               "operationId": "attempt_2", "operation": "REBUILD", "fence": 2,
                               "expiresAt": time.time() + 600, "proof": "valid-proof"}},
    })


async def test_real_etl_index_and_rebuild_reuses_vectors_and_shared_semaphore(env, tmp_path):
    settings, client, knowledge, publications = env
    path = tmp_path / "manual.txt"
    path.write_text("私有知识正文", encoding="utf-8")
    downloader, embeddings = Downloader(path), Embeddings()
    preparer = KnowledgePublicationPreparer(settings, downloader, embeddings, knowledge,
                                            semaphore=asyncio.Semaphore(1))
    document = value(settings)
    indexed = await asyncio.wait_for(preparer.prepare(index_request(settings, document)), 3)
    rebuilt = await asyncio.wait_for(preparer.prepare(rebuild_request(settings, indexed, document)), 3)
    assert embeddings.calls == 1 and downloader.calls == 2
    assert indexed.snapshot_hash == rebuilt.snapshot_hash
    assert indexed.candidate_collection != rebuilt.candidate_collection
    assert not client.aliases
    assert await publications.validate_publication(snapshot(rebuilt), verify_rows=True)


async def test_bootstrap_from_legacy_collection_reparses_and_never_embeds(env, tmp_path):
    from types import SimpleNamespace
    settings, client, knowledge, publications = env
    path = tmp_path / "manual.txt"
    path.write_text("私有知识正文", encoding="utf-8")
    document = value(settings)
    result = await knowledge.upsert_document_version(document, lease=lease("document:doc-1"))
    before = deepcopy(client.collections)
    alias_before = dict(client.aliases)
    embeddings = Embeddings()
    preparer = KnowledgePublicationPreparer(settings, Downloader(path), embeddings, knowledge)
    receipt = await preparer.prepare(rebuild_request(settings,
        SimpleNamespace(candidate_collection=result.collection_name), document, bootstrap=True))
    assert embeddings.calls == 0
    assert alias_before == client.aliases
    assert all(client.collections[name] == rows for name, rows in before.items())
    assert await publications.validate_publication(snapshot(receipt), verify_rows=True)


async def test_rebuild_changed_parsing_rejected_without_embedding(env, tmp_path):
    settings, _, knowledge, _ = env
    original = value(settings)
    # 原文件 hash 相同而旧定位不同，不能凭下载校验通过就改变已验收分块。
    original = replace(original, chunks=(replace(original.chunks[0], source_locator="old-locator"),))
    receipt = await seed(env, [original])
    path = tmp_path / "manual.txt"
    path.write_text("私有知识正文", encoding="utf-8")
    embeddings = Embeddings()
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_VERSION_CONFLICT"):
        await KnowledgePublicationPreparer(settings, Downloader(path), embeddings, knowledge).prepare(
            rebuild_request(settings, receipt, original))
    assert embeddings.calls == 0


async def test_empty_rebuild_blocked_and_empty_delete_completed(env):
    settings, _, knowledge, _ = env
    receipt = await seed(env)
    raw = rebuild_request(settings, receipt, value(settings)).model_dump(by_alias=True)
    raw["context"]["documents"] = raw["mutation"]["documents"] = []
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_EMPTY_REBUILD_BLOCKED"):
        await KnowledgePublicationPreparer(settings, None, None, knowledge).prepare(CandidateRequest.model_validate(raw))
    deleted = await KnowledgePublicationPreparer(settings, None, None, knowledge).prepare(request(receipt))
    assert deleted.documents == []


async def test_receipt_oversize_rejected_without_source_or_embedding(env):
    settings, client, knowledge, _ = env
    receipt = await seed(env)
    documents = [document_identity(replace(value(settings, f"extra-{number:04d}"), file_name="a" * 250 + ".txt"))
                 for number in range(400)]
    raw = request(receipt).model_dump(by_alias=True)
    raw["context"]["documents"] = [item.model_dump(by_alias=True) for item in documents]
    before = len(client.iterator_calls)
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_PUBLICATION_RECEIPT_TOO_LARGE"):
        await KnowledgePublicationPreparer(settings, None, None, knowledge).prepare(CandidateRequest.model_validate(raw))
    assert len(client.iterator_calls) == before


async def test_cancel_after_collection_create_keeps_unpublishable_candidate(env):
    settings, client, knowledge, _ = env
    receipt = await seed(env)
    original_call = knowledge._call
    entered = asyncio.Event()
    async def paused(method, *args, **kwargs):
        result = await original_call(method, *args, **kwargs)
        if method == "create_collection":
            entered.set()
            await asyncio.Event().wait()
        return result
    knowledge._call = paused
    before = set(client.collections)
    task = asyncio.create_task(KnowledgePublicationPreparer(settings, None, None, knowledge).prepare(request(receipt)))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    created = set(client.collections) - before
    assert len(created) == 1
    assert client.collections[created.pop()] == []
    assert not client.coordinator.active


@pytest.mark.parametrize("field,bad", [("indexRevision", 2), ("activationRevision", 2),
                                      ("documentContentHash", "f" * 64), ("content", "坏正文")])
async def test_selected_hit_metadata_and_content_corruption_rejected(env, field, bad):
    _, client, _, publications = env
    receipt = await seed(env)
    client.collections[receipt.candidate_collection][0][field] = bad
    with pytest.raises(MilvusKnowledgeError):
        await publications.search_publication([0.1, 0.9], 8, snapshot(receipt))


async def test_aggregate_vector_budget_stops_index_before_embedding(env, tmp_path):
    settings, _, knowledge, _ = env
    original = value(settings, "doc-2")
    source = await seed(env, [original])
    settings.rag_max_embedding_elements = 2
    path = tmp_path / "manual.txt"
    path.write_text("私有知识正文", encoding="utf-8")
    embeddings = Embeddings()
    raw = index_request(settings, value(settings)).model_dump(by_alias=True)
    raw["context"]["baseEpoch"] = 1
    raw["context"]["sourceCollection"] = source.candidate_collection
    raw["context"]["documents"].append(document_identity(original).model_dump(by_alias=True))
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_EMBEDDING_BUDGET_EXCEEDED"):
        await KnowledgePublicationPreparer(settings, Downloader(path), embeddings, knowledge).prepare(
            CandidateRequest.model_validate(raw))
    assert embeddings.calls == 0


@pytest.mark.parametrize("dimension", [1, 65_537])
async def test_new_publication_dimension_preflight_is_stricter_than_legacy_settings(env, dimension):
    settings, _, knowledge, _ = env
    receipt = await seed(env)
    settings.rag_embedding_dimension = dimension
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_EMBEDDING_DIMENSION_MISMATCH"):
        await KnowledgePublicationPreparer(settings, None, None, knowledge).prepare(request(receipt))


@pytest.mark.parametrize("close_fails", [False, True])
async def test_scan_cancel_during_iterator_creation_closes_returned_resource(env, close_fails):
    _, client, _, publications = env
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    class Iterator:
        def next(self):
            return []
        def close(self):
            closed.set()
            if close_fails:
                raise RuntimeError("供应商正文不应泄漏")
    def create(*_args, **_kwargs):
        started.set()
        assert release.wait(3)
        return Iterator()
    client.query_iterator = create
    task = asyncio.create_task(publications.scan("synthetic"))
    assert await asyncio.to_thread(started.wait, 2)
    task.cancel("原始取消")
    task.cancel("重复取消")
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.parametrize("primary", ["cancel", "integrity", "normal"])
async def test_scan_close_failure_preserves_primary_or_uses_stable_code(env, primary):
    _, client, _, publications = env
    started, release, closed = threading.Event(), threading.Event(), threading.Event()
    class Iterator:
        def next(self):
            if primary == "cancel":
                started.set()
                assert release.wait(3)
                return []
            if primary == "integrity":
                return [{"id": "duplicate"}, {"id": "duplicate"}]
            return []
        def close(self):
            closed.set()
            raise RuntimeError("供应商正文不应泄漏")
    client.query_iterator = lambda *_args, **_kwargs: Iterator()
    if primary == "integrity":
        # 预算异常发生在批次处理阶段，必须优先于随后 close 的供应商异常。
        def reject(_rows, **_kwargs):
            raise MilvusKnowledgeError("KNOWLEDGE_PUBLICATION_INVALID")
        publications.budget = reject
    task = asyncio.create_task(publications.scan("synthetic"))
    if primary == "cancel":
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel("原始取消")
        release.set()
        with pytest.raises(asyncio.CancelledError, match="原始取消"):
            await task
    else:
        expected = "KNOWLEDGE_PUBLICATION_INVALID" if primary == "integrity" else "KNOWLEDGE_PUBLICATION_ITERATOR_CLOSE_FAILED"
        with pytest.raises(MilvusKnowledgeError, match=f"^{expected}$"):
            await task
    assert closed.is_set()


async def test_scan_repeated_cancel_does_not_interrupt_close_or_replace_original(env):
    _, client, _, publications = env
    next_started, next_release = threading.Event(), threading.Event()
    close_started, close_release, closed = threading.Event(), threading.Event(), threading.Event()
    class Iterator:
        def next(self):
            next_started.set()
            assert next_release.wait(3)
            return []
        def close(self):
            close_started.set()
            assert close_release.wait(3)
            closed.set()
    client.query_iterator = lambda *_args, **_kwargs: Iterator()
    task = asyncio.create_task(publications.scan("synthetic"))
    assert await asyncio.to_thread(next_started.wait, 2)
    task.cancel("原始取消")
    next_release.set()
    assert await asyncio.to_thread(close_started.wait, 2)
    task.cancel("重复取消")
    assert not task.done()
    close_release.set()
    with pytest.raises(asyncio.CancelledError, match="原始取消"):
        await task
    assert closed.is_set()
