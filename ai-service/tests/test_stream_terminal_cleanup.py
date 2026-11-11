"""终态消费者立即断开时，图写入与 checkpoint 收尾必须已经排空。"""
import asyncio

import pytest

from ai_service.api.schemas import GenerationRequest
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.workflow import GenerationWorkflow
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_terminal_is_delivered_after_cleanup_even_if_consumer_closes(settings, fails):
    entered = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()
    terminal_seen = asyncio.Event()

    class Checkpoint(MemoryCheckpoint):
        async def cleanup_graph(self, thread_id):
            entered.set()
            await release.wait()
            await super().cleanup_graph(thread_id)
            cleaned.set()

    class Model(FakeModel):
        async def generate(self, branch, context):
            if fails:
                raise RuntimeError("TEST_GENERATION_FAILED")
            return await super().generate(branch, context)

    workflow = GenerationWorkflow(model=Model(), tool_gateway=FakeToolGateway(),
                                  checkpoint=Checkpoint(), cancellations=CancellationRegistry(), settings=settings)
    request = GenerationRequest(appId="42", requestId="terminal-cleanup", prompt="build a page", codeGenType="HTML")
    events = []

    async def consume():
        stream = workflow.stream(request)
        try:
            async for event in stream:
                events.append(event)
                if event.type in ("completed", "failed"):
                    terminal_seen.set()
                    assert cleaned.is_set()
                    break
        finally:
            await stream.aclose()

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), timeout=3)
        # 推进已排队的消费者；门闩未释放时不能提前发出终态。
        for _ in range(3):
            await asyncio.sleep(0)
        assert events
        assert not terminal_seen.is_set()
        release.set()
        await asyncio.wait_for(consumer, timeout=3)
        assert cleaned.is_set()
        assert [e.type for e in events if e.type in ("completed", "failed")] == ["failed" if fails else "completed"]
    finally:
        release.set()
        await asyncio.gather(consumer, return_exceptions=True)
