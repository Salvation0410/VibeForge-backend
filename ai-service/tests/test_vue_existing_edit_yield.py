"""已有应用普通修改的真实工具序列回放；交验不等于生成成功。"""

from copy import deepcopy

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph, vue_recursion_limit
from conftest import FakeModel, MemoryCheckpoint
from test_vue_repair_budget_yield import BuildGateway
from test_vue_subgraph import initial, make_workflow


class ExistingEditModel(FakeModel):
    async def generate(self, branch, context):
        self.calls.append(("generate", deepcopy(context)))
        count = len(context["toolResults"])
        if count >= 22:
            return ModelTurn("交回验证")
        writing = count == 6
        return ModelTurn("修改" if writing else "核对", tool_calls=[ToolCall(
            "file_modify" if writing else "file_read", {
                "relativeFilePath": f"src/File{count + i}.vue",
                **({"oldContent": "旧内容", "newContent": "新内容"} if writing else {}),
            }) for i in range(2)])


async def run_graph(settings, exists=True, legacy=False, checkpoint=None, interrupt_after=None):
    model, gateway = ExistingEditModel(), BuildGateway()
    graph = build_vue_tool_subgraph(
        make_workflow(settings, model, checkpoint or MemoryCheckpoint(), gateway),
        EventEmitter("subgraph-test"), "42:subgraph-test", {}, legacy=legacy,
        interrupt_after=interrupt_after,
    )
    state = {**initial(), "context": {"currentArtifact": {"exists": exists}}}
    result = await graph.ainvoke(state, {"recursion_limit": vue_recursion_limit(settings)})
    return result, model, gateway


@pytest.mark.parametrize("exists,legacy,expected", [
    (True, False, 14), (False, False, 22), (None, False, 22),
    (1, False, 22), ("true", False, 22), (True, True, 22),
])
async def test_real_sequence_yields_only_for_explicit_existing_formal_edit(settings, exists, legacy, expected):
    result, model, gateway = await run_graph(settings, exists, legacy)
    assert result["tool_call_count"] == expected
    assert len(model.calls) == (7 if expected == 14 else 12)
    assert sum(c["name"] == "file_modify" for c in gateway.calls) == 2
    assert not any(c["name"] in {"project_build", "vue_source_snapshot"} for c in gateway.calls)
    assert result["context"]["currentArtifact"]["exists"] == exists
    assert "vue_loop" not in result


@pytest.mark.parametrize("case,expected", [
    ("ready", "finish"), ("missing_baseline", "model_turn"),
    ("no_write", "model_turn"), ("old_write_missing", "model_turn"),
    ("pending_image", "model_turn"), ("pending_file", "model_turn"),
    ("partial", "execute_tool"), ("truncated", "model_turn"),
    ("failed_read", "model_turn"), ("unknown_read", "model_turn"),
    ("mixed", "model_turn"), ("reserve", "finish"),
    ("missing_count", "model_turn"), ("old_batches_missing", "model_turn"),
])
async def test_existing_edit_uses_original_complete_batch_eligibility(settings, case, expected):
    from test_vue_repair_budget_yield import ready_state
    from ai_service.orchestration.vue_tool_loop import create_vue_loop_nodes
    state = ready_state()
    state.update(repair_count=0, context={"currentArtifact": {"exists": True}})
    loop = state["vue_loop"]
    if case == "missing_baseline":
        state["context"] = {}
    if case == "no_write":
        loop["successful_write"] = False
    if case == "old_write_missing":
        loop.pop("successful_write")
    if case == "pending_image":
        loop["image_used"] = False
    if case == "pending_file":
        loop["file_plan"] = ["src/Pending.vue"]
    if case == "partial":
        loop["pending_tools"].append({"name": "file_write"})
    if case in {"truncated", "failed_read", "unknown_read"}:
        state["vue_tool_results"][0]["result"] = {
            "truncated": {"content": "部分", "truncated": True},
            "failed_read": {"ok": False, "content": "失败"}, "unknown_read": {"ok": True},
        }[case]
    if case == "mixed":
        loop["pending_tools"][0]["name"] = "dir_read"
    if case in {"reserve", "missing_count"}:
        state["tool_call_count"] = 28
        loop["read_only_batches"] = 0
    if case == "missing_count":
        state.pop("repair_count")
    if case == "old_batches_missing":
        loop.pop("read_only_batches")
    before = deepcopy(state)
    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint()),
                                  EventEmitter("subgraph-test"), "42:subgraph-test", "vue_agent",
                                  modify_budget_guard=True)
    result = await nodes["continue_loop"](state)
    assert result["vue_loop"]["next_step"] == expected
    assert state == before
    assert set(result) == {"vue_loop"}


async def test_existing_edit_resume_preserves_reads_without_repeating_write(settings):
    from test_subgraphs import CleaningGraphCheckpoint
    checkpoint, model, gateway = CleaningGraphCheckpoint(), ExistingEditModel(), BuildGateway()
    workflow = make_workflow(settings, model, checkpoint, gateway)
    graph = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {},
                                   interrupt_after=["continue_loop"])
    graph.checkpointer = checkpoint.saver
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    await graph.ainvoke({**initial(), "context": {"currentArtifact": {"exists": True}}}, config)
    for _ in range(15):
        saved = await graph.aget_state(config)
        if saved.values["vue_loop"]["read_only_batches"] == 2:
            break
        await graph.ainvoke(None, config)
    assert saved.values["vue_loop"]["successful_write"] is True
    assert saved.values["vue_loop"]["read_only_batches"] == 2
    rebuilt = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {})
    rebuilt.checkpointer = checkpoint.saver
    result = await rebuilt.ainvoke(None, config)
    assert result["tool_call_count"] == 14
    assert sum(c["name"] == "file_modify" for c in gateway.calls) == 2


async def test_handoff_still_builds_reviews_repairs_and_revalidates(settings):
    from test_subgraphs import request_for
    from test_quality_review_diagnostics import REPAIR, PASS
    from ai_service.models.single_quality_review import parse_single_review

    class Model(ExistingEditModel):
        async def review(self, artifact, context):
            self.calls.append(("review", deepcopy(context)))
            return parse_single_review(REPAIR if sum(n == "review" for n, _ in self.calls) == 1 else PASS, "STOP")

    gateway, model = BuildGateway(), Model()
    gateway.fixed = True
    gateway.artifact_context = {"exists": True}
    # 此处只验证审查驱动的正式修复，构建故障另由原有专项覆盖。
    original = gateway.invoke
    async def invoke(name, arguments, **kwargs):
        result = await original(name, arguments, **kwargs)
        return {"built": True} if name == "project_build" else result
    gateway.invoke = invoke
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == "completed"
    assert sum(e.type in {"completed", "failed"} for e in events) == 1
    assert sum(c["name"] == "project_build" for c in gateway.calls) == 2
    assert sum(n == "review" for n, _ in model.calls) == 2
    repairs = [c["context"] for n, c in model.calls if n == "repair"]
    assert repairs and repairs[0]["repairWork"]["issues"]
    assert all(c["repairCount"] == 1 for c in repairs)
    assert len([c for c in gateway.calls if c["name"] in {"file_read", "file_modify", "file_write"}]) == 15

async def test_existing_edit_reserve_executes_second_tool_and_logs_safe_counts(settings, caplog):
    import logging
    from test_vue_repair_budget_yield import ready_state
    from ai_service.orchestration.vue_tool_loop import create_vue_loop_nodes
    state = ready_state()
    state.update(repair_count=0, tool_call_count=28, context={"currentArtifact": {"exists": True}})
    state["vue_loop"]["pending_tools"].append({"name": "file_modify", "arguments": {
        "relativeFilePath": "src/Private.vue", "oldContent": "private-old", "newContent": "private-new",
    }})
    gateway = BuildGateway()
    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway),
                                  EventEmitter("subgraph-test"), "42:subgraph-test", "vue_agent",
                                  modify_budget_guard=True)
    state.update(await nodes["continue_loop"](state))
    assert state["vue_loop"]["next_step"] == "execute_tool"
    assert not gateway.calls
    state.update(await nodes["execute_tool"](state))
    with caplog.at_level(logging.INFO):
        result = await nodes["continue_loop"](state)
    assert result["vue_loop"]["next_step"] == "finish"
    assert state["tool_call_count"] == 29
    assert [c["name"] for c in gateway.calls] == ["file_modify"]
    assert "node=vue_agent" in caplog.text and "reason=NEXT_REPAIR_RESERVE" in caplog.text
    assert "private-" not in caplog.text and "src/" not in caplog.text


async def test_cancel_precedes_existing_edit_yield(settings):
    from test_vue_repair_budget_yield import ready_state
    from ai_service.orchestration.vue_tool_loop import create_vue_loop_nodes
    from ai_service.orchestration.cancellation import GenerationCancelled
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint())
    workflow.cancellations.cancel("42:subgraph-test")
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", "vue_agent",
                                  modify_budget_guard=True)
    state = ready_state()
    state["context"] = {"currentArtifact": {"exists": True}}
    with pytest.raises(GenerationCancelled):
        await nodes["continue_loop"](state)


async def test_read_only_existing_edit_does_not_arm_repair_prewrite_guard(settings):
    class ReadOnly(FakeModel):
        async def generate(self, branch, context):
            return ModelTurn("必要读取", tool_calls=[ToolCall("file_read", {"relativeFilePath": "src/App.vue"})])
    graph = build_vue_tool_subgraph(make_workflow(settings, ReadOnly(), MemoryCheckpoint(), BuildGateway()),
                                   EventEmitter("subgraph-test"), "42:subgraph-test", {})
    with pytest.raises(ValueError, match="VUE_TOOL_CALL_LIMIT_EXCEEDED"):
        await graph.ainvoke({**initial(), "context": {"currentArtifact": {"exists": True}}},
                            {"recursion_limit": vue_recursion_limit(settings)})
