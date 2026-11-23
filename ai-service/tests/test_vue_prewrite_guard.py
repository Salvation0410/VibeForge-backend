"""首次写入前仅纠偏一次，持续无新增信息失败，不能以旧候选冒充修复成功。"""

from copy import deepcopy
from dataclasses import asdict
import asyncio
import logging

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph
from ai_service.orchestration.vue_tool_loop import create_vue_loop_nodes, initial_progress
from conftest import ChangingSnapshotGateway, FakeModel, MemoryCheckpoint
from test_subgraphs import CleaningGraphCheckpoint
from test_vue_subgraph import initial, make_workflow


class Gateway(ChangingSnapshotGateway):
    async def invoke(self, name, arguments, **kwargs):
        result = await super().invoke(name, arguments, **kwargs)
        return {"content": "私有完整源码"} if name == "file_read" else result


class ReadModel(FakeModel):
    def __init__(self, *, correct=False):
        super().__init__()
        self.correct = correct

    async def repair(self, artifact, context):
        self.calls.append(("repair", deepcopy(context)))
        if self.correct and context.get("repairReadFeedback"):
            if context["vueGeneration"]["writtenFiles"]:
                return ModelTurn("交回验证")
            return ModelTurn("修复接线", tool_calls=[ToolCall("file_write", {
                "relativeFilePath": "src/App.vue", "content": "修复内容",
            })])
        return ModelTurn("读取", tool_calls=[ToolCall("file_read", {
            "relativeFilePath": path,
        }) for path in ("src/App.vue", "src/Card.vue")])


@pytest.mark.parametrize("correct", [True, False])
async def test_prewrite_repeats_warn_once_then_write_or_fail(settings, correct, caplog):
    model, gateway = ReadModel(correct=correct), Gateway()
    graph = build_vue_tool_subgraph(make_workflow(settings, model, MemoryCheckpoint(), gateway),
                                   EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    with caplog.at_level(logging.INFO):
        if correct:
            result = await graph.ainvoke(initial(), {"recursion_limit": 300})
            assert result["tool_call_count"] == 7
            assert "repairReadFeedback" not in result["context"]
        else:
            with pytest.raises(ValueError, match="VUE_REPAIR_READ_STALLED"):
                await graph.ainvoke(initial(), {"recursion_limit": 300})
    assert sum(c["name"] == "file_read" for c in gateway.calls) == (6 if correct else 10)
    assert "reason=PREWRITE_READ_WARNING" in caplog.text
    assert caplog.text.count("reason=PREWRITE_READ_WARNING") == 1
    assert "私有完整源码" not in caplog.text and "src/App.vue" not in caplog.text
    # 私有计数不进入模型；模型只接收一次固定纠偏文案。
    assert all("prewrite_warning" not in str(context) for _, context in model.calls)


async def test_warning_persists_across_checkpoint_resume(settings):
    checkpoint, model = CleaningGraphCheckpoint(), ReadModel()
    workflow = make_workflow(settings, model, checkpoint, Gateway())
    graph = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {},
                                   node="repair", interrupt_after=["continue_loop"])
    graph.checkpointer = checkpoint.saver
    config = {"configurable": {"thread_id": "42:subgraph-test"}, "recursion_limit": 300}
    await graph.ainvoke(initial(), config)
    for _ in range(10):
        saved = await graph.aget_state(config)
        if saved.values["vue_loop"].get("prewrite_warning"):
            break
        await graph.ainvoke(None, config)
    else:
        pytest.fail("未在有限重复内给出纠偏")
    rebuilt = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    rebuilt.checkpointer = checkpoint.saver
    with pytest.raises(ValueError, match="VUE_REPAIR_READ_STALLED"):
        await rebuilt.ainvoke(None, config)
    assert sum(c["name"] == "file_read" for c in workflow.tool_gateway.calls) == 10


@pytest.mark.parametrize("case", ["mixed", "partial", "generation", "new", "unknown", "written"])
async def test_guard_waits_for_complete_eligible_batch(settings, case):
    prefix = "subgraph-test:vue-repair:1"
    loop = initial_progress({}, prefix, "2026-11-23T00:00:00+00:00")
    read = {"name": "file_read", "arguments": {"relativeFilePath": "src/App.vue"}}
    loop.update(pending_tools=[read], tool_cursor=1)
    history = [{"toolCallId": f"{prefix}:{i}", "tool": "file_read",
                "arguments": read["arguments"], "result": {"content": "源码"}} for i in range(1, 7)]
    if case in {"mixed", "partial"}:
        loop["pending_tools"] = [{"name": "file_write"}, read] if case == "mixed" else [read, {"name": "file_write"}]
        loop["tool_cursor"] = 2 if case == "mixed" else 1
    if case == "new":
        history[-1]["arguments"] = {"relativeFilePath": "src/New.vue"}
    if case == "unknown":
        history[-1]["result"]["truncated"] = True
    if case == "written":
        loop["successful_write"] = True
    state = {**initial(), "vue_loop": loop, "vue_tool_results": history}
    before = deepcopy(state)
    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint()),
                                  EventEmitter("subgraph-test"), "42:subgraph-test", "repair",
                                  repair_read_guard=case != "generation")
    result = await nodes["continue_loop"](state)
    assert not result["vue_loop"].get("prewrite_warning")
    assert "repairReadFeedback" not in result.get("context", {})
    assert state == before


async def test_warned_empty_completion_fails_and_cancel_precedes_guard(settings, monkeypatch):
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint())
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_read_guard=True)
    loop = initial_progress({}, "subgraph-test:vue-repair:1", "2026-11-23T00:00:00+00:00")
    loop.update(prewrite_warning=True, turn=asdict(ModelTurn("完成", finish_reason="STOP")))
    state = {**initial(), "vue_loop": loop, "vue_tool_results": []}
    with pytest.raises(ValueError, match="VUE_REPAIR_READ_STALLED"):
        await nodes["validate_turn"](state)

    def cancelled(_):
        raise asyncio.CancelledError

    monkeypatch.setattr(workflow, "_raise_if_cancelled", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await nodes["validate_turn"](state)
    with pytest.raises(asyncio.CancelledError):
        await nodes["continue_loop"](state)
