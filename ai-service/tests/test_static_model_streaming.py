"""静态文本流与完整候选分离，禁止将截断内容或失败草稿送入发布。"""

import asyncio

import pytest
from langchain_core.messages import AIMessageChunk
from langsmith import tracing_context
from langsmith.utils import tracing_is_enabled

from ai_service.models.base import ModelTurn
from ai_service.models.openai_compatible import OpenAICompatibleModel
from conftest import FakeModel, MemoryCheckpoint
from test_vue_subgraph import make_workflow
from test_subgraphs import request_for


class ChunkClient:
    def __init__(self, *, finish="stop", fail=False):
        self.closed = False
        self.messages = None
        self.finish = finish
        self.fail = fail

    async def astream(self, messages, **kwargs):
        self.messages = messages
        try:
            assert tracing_is_enabled() is False
            yield AIMessageChunk(content="<html>")
            yield AIMessageChunk(content="页面</html>")
            if self.fail:
                raise RuntimeError("provider disconnected")
            yield AIMessageChunk(content="", response_metadata={"finish_reason": self.finish},
                                 usage_metadata={"input_tokens": 2, "output_tokens": 4, "total_tokens": 6})
        finally:
            self.closed = True


def stream_model(client):
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model._client = client
    return model


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
async def test_adapter_streams_chunks_and_keeps_full_candidate_metadata(repair):
    client = ChunkClient()
    model = stream_model(client)
    deltas = []

    async def emit(text):
        deltas.append(text)
        assert not client.closed

    with tracing_context(enabled=True):
        result = await (model.repair_stream("old", {"codeGenType": "HTML"}, on_delta=emit) if repair
                        else model.generate_stream("HTML", {}, on_delta=emit))
    assert deltas == ["<html>", "页面</html>"]
    assert result.content == "<html>页面</html>"
    assert result.finish_reason == "STOP"
    assert result.token_usage == {"input_tokens": 2, "output_tokens": 4, "total_tokens": 6}
    assert client.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_adapter_closes_stream_on_callback_cancel_or_provider_error(cancel):
    client = ChunkClient(fail=not cancel)
    model = stream_model(client)

    async def emit(text):
        if cancel:
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError if cancel else RuntimeError):
        await model.generate_stream("HTML", {}, on_delta=emit)
    assert client.closed


@pytest.mark.asyncio
async def test_adapter_rejects_vue_incremental_tool_json():
    client = ChunkClient()

    async def emit(text):
        pytest.fail("Vue 工具 JSON 不应作为静态文本增量展示")

    with pytest.raises(ValueError, match="STATIC_STREAM_UNSUPPORTED"):
        await stream_model(client).generate_stream("VUE_PROJECT", {}, on_delta=emit)
    assert client.messages is None


@pytest.mark.asyncio
async def test_adapter_checks_full_prompt_budget_before_opening_stream(settings):
    client = ChunkClient()
    model = stream_model(client)
    model._settings = settings.model_copy(update={
        "model_context_window_tokens": 1024, "model_max_tokens": 16, "model_context_overhead_tokens": 0,
    })

    async def emit(text):
        pytest.fail("超预算请求不能打开模型文本流")

    with pytest.raises(ValueError, match="INPUT_CONTEXT_BUDGET_EXCEEDED"):
        await model.generate_stream("HTML", {"prompt": "a" * 5000}, on_delta=emit)
    assert client.messages is None


@pytest.mark.asyncio
async def test_adapter_rejects_empty_stream_and_closes_it():
    class EmptyClient(ChunkClient):
        async def astream(self, messages, **kwargs):
            try:
                for chunk in []:
                    yield chunk
            finally:
                self.closed = True

    client = EmptyClient()

    async def emit(text):
        pytest.fail("空流不应发出文本")

    with pytest.raises(ValueError, match="MODEL_STREAM_INCOMPLETE"):
        await stream_model(client).generate_stream("HTML", {}, on_delta=emit)
    assert client.closed


class StreamingModel(FakeModel):
    def __init__(self, *, finish="STOP", block=False, fail=False, reviews=None):
        super().__init__(reviews=reviews)
        self.finish = finish
        self.block = block
        self.fail = fail
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def generate_stream(self, branch, context, *, on_delta):
        self.calls.append(("generate_stream", context))
        await on_delta("<html>")
        self.entered.set()
        if self.block:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("provider disconnected")
        await on_delta("页面</html>")
        return ModelTurn("<html>页面</html>", finish_reason=self.finish, token_usage={"output_tokens": 4})

    async def repair_stream(self, artifact, context, *, on_delta):
        self.calls.append(("repair_stream", context))
        await on_delta("<html>修复")
        await on_delta("完成</html>")
        return ModelTurn("<html>修复完成</html>", finish_reason=self.finish)


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE"])
async def test_display_arrives_before_model_finishes_and_candidate_is_exact(branch, settings):
    model = StreamingModel(block=True)
    workflow = make_workflow(settings, model, MemoryCheckpoint())
    stream = workflow.stream(request_for(branch))
    events = []
    try:
        async with asyncio.timeout(5):
            async for event in stream:
                events.append(event)
                if event.data.get("contentMode") == "display_delta":
                    if not any(item.data.get("contentMode") == "display_delta" for item in events[:-1]):
                        assert not model.release.is_set()
                        assert not any(item.data.get("contentMode") == "candidate" for item in events)
                    model.release.set()
    finally:
        model.release.set()
        await stream.aclose()
    assert events[-1].type == "completed"
    content_events = [event for event in events if event.type == "content_delta"]
    assert [event.data["contentMode"] for event in content_events] == ["display_delta", "display_delta", "candidate"]
    assert content_events[-1].data["content"] == "<html>页面</html>"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    published = next(call for call in workflow.tool_gateway.calls if call["name"] == "artifact_publish")
    assert published["arguments"]["artifact"] == "<html>页面</html>"


@pytest.mark.asyncio
@pytest.mark.parametrize("finish,code", [("LENGTH", "MODEL_OUTPUT_TRUNCATED"),
                                        ("CONTENT_FILTER", "MODEL_OUTPUT_BLOCKED"),
                                        (None, "MODEL_STREAM_INCOMPLETE")])
async def test_incomplete_stream_can_display_but_never_emits_candidate_or_publishes(settings, finish, code):
    workflow = make_workflow(settings, StreamingModel(finish=finish), MemoryCheckpoint())
    events = await workflow.run(request_for("HTML"))
    assert events[-1].error.code == code
    assert any(event.data.get("contentMode") == "display_delta" for event in events)
    assert not any(event.data.get("contentMode") == "candidate" for event in events)
    assert not any(call["name"] in {"artifact_validate", "artifact_publish"} for call in workflow.tool_gateway.calls)


@pytest.mark.asyncio
async def test_static_repair_uses_complete_new_candidate_and_keeps_round_metadata(settings):
    workflow = make_workflow(settings, StreamingModel(reviews=[False, True]), MemoryCheckpoint())
    events = await workflow.run(request_for("MULTI_FILE"))
    assert events[-1].type == "completed"
    repair_events = [event for event in events if event.node == "repair" and event.type == "content_delta"]
    assert [event.data["contentMode"] for event in repair_events] == ["display_delta", "display_delta", "candidate"]
    assert all(event.data["repairCount"] == 1 for event in repair_events)
    published = next(call for call in workflow.tool_gateway.calls if call["name"] == "artifact_publish")
    assert published["arguments"]["artifact"] == "<html>修复完成</html>"


@pytest.mark.asyncio
async def test_failed_provider_does_not_fallback_or_publish(settings):
    model = StreamingModel(fail=True)
    workflow = make_workflow(settings, model, MemoryCheckpoint())
    events = await workflow.run(request_for("HTML"))
    assert events[-1].type == "failed"
    assert not any(name == "generate" for name, _ in model.calls)
    assert not any(call["name"] == "artifact_publish" for call in workflow.tool_gateway.calls)


@pytest.mark.asyncio
async def test_disconnect_cancels_active_model_stream_and_cleans_request(settings):
    model = StreamingModel(block=True)
    checkpoint = MemoryCheckpoint()
    workflow = make_workflow(settings, model, checkpoint)
    stream = workflow.stream(request_for("HTML"))
    async with asyncio.timeout(5):
        async for event in stream:
            if event.data.get("contentMode") == "display_delta":
                await model.entered.wait()
                break
        await stream.aclose()
    assert checkpoint.cleaned_graph_threads == ["42:subgraph-test"]
    assert not any(call["name"] == "artifact_publish" for call in workflow.tool_gateway.calls)


@pytest.mark.asyncio
async def test_truncated_repair_never_reuses_first_round_candidate(settings):
    class TruncatedRepairModel(StreamingModel):
        async def repair_stream(self, artifact, context, *, on_delta):
            await on_delta("partial repair")
            return ModelTurn("partial repair", finish_reason="LENGTH")

    workflow = make_workflow(settings, TruncatedRepairModel(reviews=[False]), MemoryCheckpoint())
    events = await workflow.run(request_for("HTML"))
    assert events[-1].error.code == "MODEL_OUTPUT_TRUNCATED"
    assert not any(event.node == "repair" and event.data.get("contentMode") == "candidate" for event in events)
    assert not any(call["name"] == "artifact_publish" for call in workflow.tool_gateway.calls)


@pytest.mark.asyncio
async def test_vue_keeps_complete_tool_loop_even_when_model_supports_text_streaming(settings):
    model = StreamingModel()
    workflow = make_workflow(settings, model, MemoryCheckpoint())
    events = await workflow.run(request_for("VUE_PROJECT"))
    assert events[-1].type == "completed"
    assert not any(name.endswith("_stream") for name, _ in model.calls)
