"""准备资格失联与取消的离线故障矩阵；不证明真实供应商计费语义。"""
import asyncio
from unittest.mock import AsyncMock

import pytest

from ai_service.api.knowledge_publication_schemas import PrepareExecutionResult
from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError
from ai_service.orchestration.knowledge_publication import KnowledgePublicationPreparer
from test_knowledge_publication import env, value, Downloader, Embeddings
from test_knowledge_prepare_recovery import anchored, retry


class Arbitration:
    """替身先保存授权/收据，再注入确认丢失，不用异常推断未提交。"""
    def __init__(self, *, lose_begin=False, lose_complete=False):
        self.calls = []
        self.granted = False
        self.receipt = None
        self.lose_begin, self.lose_complete = lose_begin, lose_complete

    async def arbitrate_prepare(self, request):
        self.calls.append(request.status)
        if request.status == "COMPLETE":
            self.receipt = request.receipt
            if self.lose_complete:
                raise MilvusKnowledgeError("KNOWLEDGE_PUBLICATION_UNAVAILABLE")
            return PrepareExecutionResult(granted=False, status="COMPLETE")
        if self.granted:
            return PrepareExecutionResult(granted=False, status="BUSY")
        self.granted = True
        if self.lose_begin:
            raise MilvusKnowledgeError("KNOWLEDGE_PUBLICATION_UNAVAILABLE")
        return PrepareExecutionResult(granted=True, status="STARTED")


@pytest.fixture
def boundary(monkeypatch):
    # 夹具无权威head；只隔离Spring校验，真实持久仲裁由Java资源测试覆盖。
    check = AsyncMock()
    monkeypatch.setattr("ai_service.orchestration.knowledge_publication_boundary.validate_candidate_request", check)
    return check


async def test_committed_grant_response_loss_never_regrants_or_embeds(env, tmp_path, boundary):
    settings, client, store, _ = env
    path = tmp_path / "manual.txt"; path.write_text("私有知识正文", encoding="utf8")
    embeddings = Embeddings(); control = Arbitration(lose_begin=True)
    request = anchored(settings, value(settings))
    for code in ["KNOWLEDGE_PUBLICATION_UNAVAILABLE", "KNOWLEDGE_PREPARE_BUSY"]:
        with pytest.raises(MilvusKnowledgeError, match=code):
            await KnowledgePublicationPreparer(settings, Downloader(path), embeddings, store, arbitration=control).prepare(request)
    assert control.granted and control.calls == ["BEGIN", "BEGIN"]
    assert embeddings.calls == 0 and client.collections == {}


@pytest.mark.parametrize("phase", ["download", "embedding", "after-embedding"])
async def test_cancelled_or_failed_executor_never_recomputes(env, tmp_path, boundary, phase):
    settings, client, store, _ = env
    path = tmp_path / "manual.txt"; path.write_text("私有知识正文", encoding="utf8")
    entered = asyncio.Event(); release = asyncio.Event()
    control = Arbitration(); embeddings = Embeddings()
    class BlockingDownload(Downloader):
        async def download(self, *args, **kwargs):
            entered.set(); await asyncio.wait_for(release.wait(), 5)
            return await super().download(*args, **kwargs)
    class BlockingEmbedding(Embeddings):
        async def embed_documents(self, texts, **kwargs):
            self.calls += 1; entered.set(); await asyncio.wait_for(release.wait(), 5)
            return [[.1, .9] for _ in texts]
    if phase == "embedding": embeddings = BlockingEmbedding()
    preparer = KnowledgePublicationPreparer(settings, BlockingDownload(path) if phase == "download" else Downloader(path),
                                            embeddings, store, arbitration=control)
    request = anchored(settings, value(settings))
    if phase == "after-embedding":
        preparer.publications.write = AsyncMock(side_effect=MilvusKnowledgeError("KNOWLEDGE_PUBLICATION_UNAVAILABLE"))
        with pytest.raises(MilvusKnowledgeError): await preparer.prepare(request)
    else:
        task = asyncio.create_task(preparer.prepare(request))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        finally:
            release.set(); await asyncio.gather(task, return_exceptions=True)
    assert control.calls == ["BEGIN"] and control.granted
    count = embeddings.calls
    assert count == (0 if phase == "download" else 1)
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_PREPARE_BUSY"):
        await KnowledgePublicationPreparer(settings, Downloader(path), embeddings, store, arbitration=control).prepare(request)
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_PREPARE_RESULT_MISSING"):
        await KnowledgePublicationPreparer(settings, Downloader(path), embeddings, store, arbitration=control).prepare(retry(request))
    assert embeddings.calls == count and control.calls == ["BEGIN", "BEGIN"]
    assert client.collections == {}


async def test_completion_persisted_response_loss_recovers_without_new_embedding(env, tmp_path, boundary):
    settings, _, store, publications = env
    path = tmp_path / "manual.txt"; path.write_text("私有知识正文", encoding="utf8")
    embeddings = Embeddings(); control = Arbitration(lose_complete=True)
    request = anchored(settings, value(settings))
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_PUBLICATION_UNAVAILABLE"):
        await KnowledgePublicationPreparer(settings, Downloader(path), embeddings, store, arbitration=control).prepare(request)
    assert control.receipt is not None and control.calls == ["BEGIN", "COMPLETE"]
    receipt, _ = await publications.completed(control.receipt.candidate_collection)
    assert receipt == control.receipt
    fresh = KnowledgePublicationPreparer(settings, Downloader(path), embeddings, store, arbitration=control)
    assert await fresh.prepare(request) == receipt
    recovered = await fresh.prepare(retry(request))
    assert recovered.snapshot_hash == receipt.snapshot_hash and recovered.fence == 2
    assert embeddings.calls == 1 and control.calls == ["BEGIN", "COMPLETE"]


async def test_epoch_revoked_after_grant_before_payment_blocks_embedding(env, tmp_path, boundary):
    settings, client, store, _ = env
    path = tmp_path / "manual.txt"; path.write_text("私有知识正文", encoding="utf8")
    boundary.side_effect = MilvusKnowledgeError("KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT")
    control = Arbitration(); embeddings = Embeddings()
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT"):
        await KnowledgePublicationPreparer(settings, Downloader(path), embeddings, store, arbitration=control).prepare(anchored(settings, value(settings)))
    assert control.calls == ["BEGIN"] and control.granted
    assert embeddings.calls == 0 and client.collections == {}


async def test_lease_revoked_during_download_blocks_payment(env, tmp_path, boundary):
    settings, client, store, _ = env
    path = tmp_path / "manual.txt"; path.write_text("私有知识正文", encoding="utf8")
    class RevokingDownload(Downloader):
        async def download(self, *args, **kwargs):
            result = await super().download(*args, **kwargs)
            client.coordinator.revoked = True
            return result
    control = Arbitration(); embeddings = Embeddings()
    with pytest.raises(MilvusKnowledgeError, match="KNOWLEDGE_MUTATION_LEASE_INVALID"):
        await KnowledgePublicationPreparer(settings, RevokingDownload(path), embeddings, store, arbitration=control).prepare(anchored(settings, value(settings)))
    assert control.calls == ["BEGIN"] and embeddings.calls == 0
    assert client.collections == {} and client.coordinator.active == set()
