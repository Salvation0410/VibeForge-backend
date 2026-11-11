"""终态消费者立即断开时，图写入与 checkpoint 收尾必须已经排空。"""
import asyncio

import pytest

from ai_service.api.schemas import GenerationRequest
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.workflow import GenerationWorkflow
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint


class CleanupGate(MemoryCheckpoint):
    """把清理停在可观测的边界；记录被二次取消打断，而非依赖运行速度。"""

    def __init__(self, *, fails=False):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.interrupted = asyncio.Event()
        self.finished = asyncio.Event()
        self.fails = fails

    async def cleanup_graph(self, thread_id):
        self.entered.set()
        try:
            await self.release.wait()
            if self.fails:
                raise RuntimeError("TEST_CLEANUP_FAILED")
            await super().cleanup_graph(thread_id)
        except asyncio.CancelledError:
            self.interrupted.set()
            raise
        finally:
            self.finished.set()


class ExitingModel(FakeModel):
    """模拟收到第一次取消后仍需异步关闭供应商连接的模型。"""

    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.exiting = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.interrupted = asyncio.Event()

    async def generate(self, branch, context):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.exiting.set()
            try:
                await self.release.wait()
                self.closed.set()
            except asyncio.CancelledError:
                self.interrupted.set()
                raise


async def advance_ready_tasks():
    # 只让已就绪任务处理取消；成功与退出的先后由门闩决定。
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_terminal_protects_graph_writes_before_ainvoke_returns(settings, terminal):
    entered, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Workflow(GenerationWorkflow):
        def _build_graph(self, emitter, thread_id, state):
            class Graph:
                async def ainvoke(self, initial, config):
                    await emitter.emit(terminal, "test")
                    entered.set()
                    await release.wait()
                    finished.set()
            return Graph()

    checkpoint = MemoryCheckpoint()
    workflow = Workflow(model=FakeModel(), tool_gateway=FakeToolGateway(), checkpoint=checkpoint,
                        cancellations=CancellationRegistry(), settings=settings)
    request = GenerationRequest(appId="42", requestId="graph-drain", prompt="build", codeGenType="HTML")

    async def consume():
        async for _ in workflow.stream(request):
            pass

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(entered.wait(), 3)
        workflow.active_generations.cancel("42:graph-drain")
        consumer.cancel()
        await advance_ready_tasks()
        assert not consumer.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(consumer, 3)
        assert finished.is_set()
        assert checkpoint.cleaned_graph_threads == ["42:graph-drain"]
    finally:
        release.set()
        if not consumer.done():
            consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.asyncio
async def test_conflicting_registration_does_not_start_or_cancel_other_execution(settings):
    model = ExitingModel()
    checkpoint = MemoryCheckpoint()
    workflow = GenerationWorkflow(model=model, tool_gateway=FakeToolGateway(), checkpoint=checkpoint,
                                  cancellations=CancellationRegistry(), settings=settings)
    request = GenerationRequest(appId="42", requestId="duplicate", prompt="build", codeGenType="HTML")
    thread_id = "42:duplicate"

    async def consume():
        return [event async for event in workflow.stream(request)]

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(model.started.wait(), 3)
        with pytest.raises(RuntimeError, match="already active"):
            await consume()
        await advance_ready_tasks()
        assert not model.exiting.is_set()
        assert workflow.active_generations.is_active(thread_id)
        assert not checkpoint.cleaned_graph_threads
        workflow.active_generations.cancel(thread_id)
        model.release.set()
        events = await asyncio.wait_for(consumer, 3)
        assert events[-1].error.code == "cancelled"
        assert checkpoint.cleaned_graph_threads == [thread_id]
    finally:
        model.release.set()
        if not consumer.done():
            consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.asyncio
async def test_old_owner_cannot_cancel_or_clear_replacement():
    from ai_service.orchestration.active_generations import ActiveGenerationRegistry

    registry = ActiveGenerationRegistry()
    old = asyncio.create_task(asyncio.sleep(0))
    registry.register("same-thread", old)
    await old
    replacement = asyncio.create_task(asyncio.Event().wait())
    registry.register("same-thread", replacement)
    try:
        registry.begin_draining("same-thread", old)
        assert not registry.cancel("same-thread", old)
        registry.clear("same-thread", old)
        assert registry.is_active("same-thread")
        assert registry.cancel("same-thread")
        assert replacement.cancelling() == 1
        assert registry.cancel("same-thread")
        assert replacement.cancelling() == 1
    finally:
        replacement.cancel()
        await asyncio.gather(replacement, return_exceptions=True)
        registry.clear("same-thread", replacement)


@pytest.mark.asyncio
async def test_unexpected_trace_failure_still_cleans_and_releases_registration(settings):
    class Tracer:
        async def record(self, *, name, **kwargs):
            if name == "generation":
                raise RuntimeError("TEST_TRACE_FAILED")

    checkpoint = MemoryCheckpoint()
    cancellations = CancellationRegistry()
    cancellations.cancel("42:trace-failure")
    workflow = GenerationWorkflow(model=FakeModel(), tool_gateway=FakeToolGateway(), checkpoint=checkpoint,
                                  cancellations=cancellations, settings=settings, tracer=Tracer())
    request = GenerationRequest(appId="42", requestId="trace-failure", prompt="build", codeGenType="HTML")
    with pytest.raises(RuntimeError, match="TEST_TRACE_FAILED"):
        async for _ in workflow.stream(request):
            pass
    assert checkpoint.cleaned_graph_threads == ["42:trace-failure"]
    assert not workflow.active_generations.is_active("42:trace-failure")
    assert not cancellations.is_cancelled("42:trace-failure")


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["stop", "consumer", "mixed"])
async def test_repeated_cancel_drains_model_and_checkpoint(settings, source):
    model = ExitingModel()
    checkpoint = CleanupGate()
    workflow = GenerationWorkflow(model=model, tool_gateway=FakeToolGateway(),
                                  checkpoint=checkpoint, cancellations=CancellationRegistry(), settings=settings)
    request = GenerationRequest(appId="42", requestId="repeat-cancel", prompt="build", codeGenType="HTML")
    thread_id = "42:repeat-cancel"
    events = []

    async def consume():
        async for event in workflow.stream(request):
            events.append(event)

    consumer = asyncio.create_task(consume())

    def stop():
        workflow.cancellations.cancel(thread_id)
        assert workflow.active_generations.cancel(thread_id)

    try:
        await asyncio.wait_for(model.started.wait(), 3)
        if source == "consumer":
            consumer.cancel()
        else:
            stop()
        await asyncio.wait_for(model.exiting.wait(), 3)
        if source == "stop":
            stop()
        else:
            consumer.cancel()
        await advance_ready_tasks()
        assert not model.interrupted.is_set()
        assert not consumer.done()
        model.release.set()
        await asyncio.wait_for(checkpoint.entered.wait(), 3)
        stop()
        if source != "stop":
            consumer.cancel()
        await advance_ready_tasks()
        assert not checkpoint.interrupted.is_set()
        assert workflow.active_generations.is_active(thread_id)
        checkpoint.release.set()
        if source == "stop":
            await asyncio.wait_for(consumer, 3)
            assert [(e.type, e.error.code) for e in events if e.type in ("completed", "failed")] == [
                ("failed", "cancelled")]
        else:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 3)
        assert model.closed.is_set()
        assert checkpoint.cleaned_graph_threads == [thread_id]
        assert not workflow.active_generations.is_active(thread_id)
        assert not workflow.cancellations.is_cancelled(thread_id)
    finally:
        model.release.set()
        checkpoint.release.set()
        if not consumer.done():
            consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("disconnect", [False, True])
async def test_cancel_during_terminal_cleanup_preserves_cleanup(settings, fails, disconnect):
    checkpoint = CleanupGate(fails=True)

    class Model(FakeModel):
        async def generate(self, branch, context):
            if fails:
                raise RuntimeError("TEST_GENERATION_FAILED")
            return await super().generate(branch, context)

    workflow = GenerationWorkflow(model=Model(), tool_gateway=FakeToolGateway(),
                                  checkpoint=checkpoint, cancellations=CancellationRegistry(), settings=settings)
    request = GenerationRequest(appId="42", requestId="late-cancel", prompt="build", codeGenType="HTML")
    thread_id = "42:late-cancel"
    events = []

    async def consume():
        async for event in workflow.stream(request):
            events.append(event)

    consumer = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(checkpoint.entered.wait(), 3)
        workflow.cancellations.cancel(thread_id)
        workflow.active_generations.cancel(thread_id)
        if disconnect:
            consumer.cancel()
        await advance_ready_tasks()
        assert not checkpoint.interrupted.is_set()
        assert not consumer.done()
        checkpoint.release.set()
        if disconnect:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 3)
        else:
            await asyncio.wait_for(consumer, 3)
            assert [e.type for e in events if e.type in ("completed", "failed")] == [
                "failed" if fails else "completed"]
        assert checkpoint.finished.is_set()
        assert not workflow.active_generations.is_active(thread_id)
        assert not workflow.cancellations.is_cancelled(thread_id)
    finally:
        checkpoint.release.set()
        if not consumer.done():
            consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


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
