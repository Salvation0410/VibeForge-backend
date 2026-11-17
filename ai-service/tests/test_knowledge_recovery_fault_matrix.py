"""用显式屏障暴露首次并发付费窗口，完成重传与未知结果严格区分。"""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from ai_service.infrastructure.milvus_knowledge import MilvusKnowledgeError, MilvusKnowledgeStore
from ai_service.orchestration.knowledge_publication import KnowledgePublicationPreparer
from test_knowledge_publication import env, value, Downloader
from test_knowledge_prepare_recovery import anchored


class CurrentPermit:
    """允许同一有效lease并发进入，接近HTTP适配器而非Fake串行锁。"""
    @asynccontextmanager
    async def hold(self, lease, **kwargs):
        async def current():
            return None
        yield SimpleNamespace(fence=lease.fence, assert_current=current)


async def test_two_initial_requests_can_embed_twice_but_never_overwrite(env, tmp_path):
    settings, client, _, publications = env
    path = tmp_path / "manual.txt"
    path.write_text("私有知识正文", encoding="utf8")
    request = anchored(settings, value(settings))
    both = asyncio.Event()
    first_finished = asyncio.Event()
    calls = 0

    class Embeddings:
        def __init__(self, second):
            self.second = second
        async def embed_documents(self, texts, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 5)
            if self.second:
                await asyncio.wait_for(first_finished.wait(), 5)
            return [[0.1, 0.9] for _ in texts]

    stores = [MilvusKnowledgeStore(settings, client_factory=lambda **_: client,
              mutation_coordinator=CurrentPermit()) for _ in range(2)]
    preparers = [KnowledgePublicationPreparer(settings, Downloader(path), Embeddings(bool(i)), store)
                 for i, store in enumerate(stores)]
    async def first():
        try:
            return await preparers[0].prepare(request)
        finally:
            first_finished.set()
    tasks = [asyncio.create_task(first()), asyncio.create_task(preparers[1].prepare(request))]
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
        assert not isinstance(results[0], Exception)
        assert isinstance(results[1], MilvusKnowledgeError)
        assert results[1].code == "KNOWLEDGE_PUBLICATION_NAME_CONFLICT"
        receipt = results[0]
        assert calls == 2
        assert len(client.collections) == 1
        before = await publications.scan(receipt.candidate_collection)
        assert await preparers[1].prepare(request) == receipt
        assert calls == 2
        assert await publications.scan(receipt.candidate_collection) == before
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for store in stores:
            await store.close()
