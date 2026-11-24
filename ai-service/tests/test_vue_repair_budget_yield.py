"""用穿插新文件的真实图路径验证读取收口，交回后仍须构建和审查。"""

from copy import deepcopy
import logging

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph
from ai_service.orchestration.subgraphs.vue import vue_recursion_limit
from ai_service.orchestration.vue_tool_loop import create_vue_loop_nodes, initial_progress
from conftest import ChangingSnapshotGateway, FakeModel, MemoryCheckpoint
from test_subgraphs import CleaningGraphCheckpoint, request_for
from test_vue_subgraph import initial, make_workflow


class ReadingModel(FakeModel):
    async def generate(self, branch, context):
        results = context["toolResults"]
        if len(results) >= 14:
            return ModelTurn("写入完成")
        return ModelTurn("写入", tool_calls=[ToolCall("file_write", {
            "relativeFilePath": f"src/File{i}.vue", "content": "<template>页面</template>",
        }) for i in range(len(results), len(results) + 2)])

    async def repair(self, artifact, context):
        self.calls.append(("repair", deepcopy(context)))
        prefix = f"{context['requestId']}:vue-repair:{context['repairCount']}:"
        results = [r for r in context["toolResults"] if r["toolCallId"].startswith(prefix)]
        if not results:
            return ModelTurn("补齐属性", tool_calls=[ToolCall("file_write", {
                "relativeFilePath": "src/App.vue", "content": f"修复{context['repairCount']}",
            })])
        # 每批读取一个新文件，四次连续重复保护始终不会触发。
        return ModelTurn("确认", tool_calls=[ToolCall("file_read", {
            "relativeFilePath": f"src/Read{len(results) + i}.vue",
        }) for i in range(2)])


class BuildGateway(ChangingSnapshotGateway):
    def __init__(self, fixed=True):
        super().__init__()
        self.fixed = fixed

    async def invoke(self, name, arguments, **kwargs):
        result = await super().invoke(name, arguments, **kwargs)
        if name == "project_build":
            builds = sum(c["name"] == name for c in self.calls)
            if builds == 1 or not self.fixed:
                return {"built": False, "errorCode": "VUE_COMPONENT_CONTRACT_FAILED", "message": "缺少属性"}
        if name == "file_read":
            return {"content": arguments["relativeFilePath"]}
        return result


@pytest.mark.parametrize("fixed", [True, False])
async def test_new_files_interleaved_with_reads_yield_to_real_validation(settings, fixed):
    model, gateway = ReadingModel(), BuildGateway(fixed)
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == ("completed" if fixed else "failed")
    if not fixed:
        assert events[-1].error.code == "VUE_COMPONENT_CONTRACT_FAILED"
    assert sum(c["name"] == "project_build" for c in gateway.calls) == (2 if fixed else 3)
    repairs = [c for c in gateway.calls if ":vue-repair:" in c["toolCallId"]
               and c["name"] != "vue_source_snapshot"]
    assert len(repairs) == (7 if fixed else 14)
    assert sum(name == "repair" for name, _ in model.calls) == (4 if fixed else 8)
    assert sum(e.type in {"completed", "failed"} for e in events) == 1
    first_turns = [context for name, context in model.calls if name == "repair"
                   and not any(r["toolCallId"].startswith(
                       f"subgraph-test:vue-repair:{context['repairCount']}:") for r in context["toolResults"])]
    assert [c["vueGeneration"]["remainingToolCalls"] for c in first_turns] == ([26] if fixed else [26, 19])
    assert all("read_only_batches" not in c and "successful_write" not in c for _, c in model.calls)


def ready_state():
    loop = initial_progress({}, "subgraph-test:vue-repair:1", "2026-11-12T00:00:00+00:00")
    loop.update(successful_write=True, read_only_batches=2,
                pending_tools=[{"name": "file_read", "arguments": {"relativeFilePath": "src/New.vue"}}],
                tool_cursor=1, ordinal=8)
    return {**initial(), "repair_count": 2, "vue_loop": loop, "tool_call_count": 20, "vue_tool_results": [{
        "toolCallId": "subgraph-test:vue-repair:1:8", "tool": "file_read",
        "arguments": {"relativeFilePath": "src/New.vue"}, "result": {"content": "新文件"},
    }]}


@pytest.mark.parametrize("case,expected", [
    ("ready", "finish"), ("no_write", "model_turn"), ("pending_image", "model_turn"),
    ("pending_file", "model_turn"), ("partial_batch", "execute_tool"),
    ("disabled", "model_turn"), ("unknown_read", "model_turn"),
    ("mixed_batch", "model_turn"), ("two_batches", "model_turn"),
    ("low_budget", "finish"), ("five_remaining", "model_turn"),
])
async def test_budget_guard_requires_completed_eligible_repair(settings, case, expected):
    state = ready_state()
    loop = state["vue_loop"]
    if case == "no_write":
        loop["successful_write"] = False
    if case == "pending_image":
        loop["image_used"] = False
    if case == "pending_file":
        loop["file_plan"] = ["src/Unwritten.vue"]
    if case == "partial_batch":
        loop["pending_tools"].append({"name": "file_write"})
    if case == "unknown_read":
        state["vue_tool_results"][0]["result"] = {"content": "片段", "truncated": True}
    if case == "mixed_batch":
        loop["pending_tools"][0]["name"] = "dir_read"
    if case in {"two_batches", "low_budget", "five_remaining"}:
        loop["read_only_batches"] = 1
        state["tool_call_count"] = {"low_budget": 36, "five_remaining": 35}.get(case, 20)
    before = deepcopy(state)
    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint()),
                                  EventEmitter("subgraph-test"), "42:subgraph-test", "repair",
                                  repair_budget_guard=case != "disabled")
    result = await nodes["continue_loop"](state)
    assert result["vue_loop"]["next_step"] == expected
    assert state == before
    assert set(result) == {"vue_loop"}
    if case in {"mixed_batch", "unknown_read"}:
        assert result["vue_loop"]["read_only_batches"] == 0


@pytest.mark.parametrize("repair_count,tool_count", [(1, 28), (2, 36)])
async def test_cancel_wins_over_budget_yield(settings, repair_count, tool_count):
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint())
    workflow.cancellations.cancel("42:subgraph-test")
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_budget_guard=True)
    state = ready_state()
    state.update(repair_count=repair_count, tool_call_count=tool_count)
    with pytest.raises(GenerationCancelled):
        await nodes["continue_loop"](state)


async def test_exhaustion_logs_actual_counts_without_source_or_paths(settings, caplog):
    model = FakeModel(vue_tool_calls=41)
    with caplog.at_level(logging.WARNING):
        events = await make_workflow(settings, model, MemoryCheckpoint()).run(request_for("VUE_PROJECT"))
    assert events[-1].error.code == "VUE_TOOL_CALL_LIMIT_EXCEEDED"
    assert "tool_count=40" in caplog.text
    assert "read_count=40" in caplog.text
    assert "requested_count=1" in caplog.text
    assert "remaining=0" in caplog.text
    assert "src/App.vue" not in caplog.text


async def test_checkpoint_resume_preserves_read_batches_and_stage_write(settings):
    model, gateway, checkpoint = ReadingModel(), BuildGateway(), CleaningGraphCheckpoint()
    workflow = make_workflow(settings, model, checkpoint, gateway)
    emitter = EventEmitter("subgraph-test")
    graph = build_vue_tool_subgraph(workflow, emitter, "42:subgraph-test", {}, node="repair",
                                   interrupt_after=["continue_loop"])
    # 独立测试图显式使用 saver；生产子图从父图继承，不创建额外 saver。
    graph.checkpointer = checkpoint.saver
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    await graph.ainvoke({**initial(), "tool_call_count": 14, "context": {"requestId": "subgraph-test"}}, config)
    # 两工具批次会在中间工具后也暂停，只有完整批次才增加计数。
    observed = []
    for _ in range(5):
        saved = await graph.aget_state(config)
        assert saved.values["vue_loop"]["successful_write"] is True
        observed.append(saved.values["vue_loop"]["read_only_batches"])
        if observed[-1] == 2:
            break
        await graph.ainvoke(None, config)
    assert observed == [0, 0, 1, 1, 2]
    rebuilt = build_vue_tool_subgraph(workflow, emitter, "42:subgraph-test", {}, node="repair")
    rebuilt.checkpointer = checkpoint.saver
    result = await rebuilt.ainvoke(None, config)
    assert result["tool_call_count"] == 21
    assert result["repair_count"] == 1
    assert "vue_loop" not in result
    assert sum(c["name"] == "file_write" for c in gateway.calls) == 1


@pytest.mark.parametrize("ok", [True, False, None, 1])
async def test_only_explicit_success_arms_phase_yield(settings, ok):
    class Gateway(ChangingSnapshotGateway):
        async def invoke(self, name, arguments, **kwargs):
            await super().invoke(name, arguments, **kwargs)
            return {} if ok is None else {"ok": ok}

    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint(), Gateway()),
                                  EventEmitter("subgraph-test"), "42:subgraph-test", "repair",
                                  repair_budget_guard=True)
    state = ready_state()
    state["vue_loop"].update(successful_write=False, tool_cursor=0, pending_tools=[{
        "name": "file_write", "arguments": {"relativeFilePath": "src/App.vue", "content": "修复"},
    }])
    result = await nodes["execute_tool"](state)
    assert result["vue_loop"]["successful_write"] is (ok is True)


async def test_low_budget_does_not_skip_write_after_read_in_same_batch(settings):
    gateway = BuildGateway()
    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway),
                                  EventEmitter("subgraph-test"), "42:subgraph-test", "repair",
                                  repair_budget_guard=True)
    state = ready_state()
    state["tool_call_count"] = 36
    state["vue_loop"]["pending_tools"].append({"name": "file_write", "arguments": {
        "relativeFilePath": "src/App.vue", "content": "必要的后续写入",
    }})
    state.update(await nodes["continue_loop"](state))
    assert state["vue_loop"]["next_step"] == "execute_tool"
    assert not gateway.calls
    state.update(await nodes["execute_tool"](state))
    result = await nodes["continue_loop"](state)
    assert result["vue_loop"]["next_step"] == "finish"
    assert result["vue_loop"]["read_only_batches"] == 0
    assert [c["name"] for c in gateway.calls] == ["file_write"]
    assert state["tool_call_count"] == 37


async def test_read_only_repair_without_successful_write_still_exhausts_budget(settings):
    class Model(FakeModel):
        async def repair(self, artifact, context):
            return ModelTurn("读取", tool_calls=[ToolCall("file_read", {"relativeFilePath": "src/App.vue"})])

    graph = build_vue_tool_subgraph(make_workflow(settings, Model(), MemoryCheckpoint(), BuildGateway()),
                                   EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    with pytest.raises(ValueError, match="VUE_TOOL_CALL_LIMIT_EXCEEDED"):
        await graph.ainvoke({**initial(), "tool_call_count": 36},
                            {"recursion_limit": vue_recursion_limit(settings)})


async def test_yield_keeps_quality_rejection_and_shared_repair_limit(settings):
    model, gateway = ReadingModel(reviews=[False, False]), BuildGateway()
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == "failed"
    assert events[-1].error.code == "QUALITY_REVIEW_FAILED"
    assert sum(c["name"] == "project_build" for c in gateway.calls) == 3
    assert sum(name == "review" for name, _ in model.calls) == 2
    assert max(c["repairCount"] for name, c in model.calls if name == "repair") == 2


async def test_failed_repair_logs_current_phase_counts_and_rejects_entire_next_batch(settings, caplog):
    class PendingModel(ReadingModel):
        async def repair(self, artifact, context):
            turn = await super().repair(artifact, context)
            turn.file_plan = ["src/StillRequired.vue"]
            return turn

    model, gateway = PendingModel(), BuildGateway()
    with caplog.at_level(logging.WARNING):
        events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].error.code == "VUE_TOOL_CALL_LIMIT_EXCEEDED"
    # 只剩一次额度但请求两个，整批拒绝，不能执行第一个后再失败。
    assert "tool_count=39" in caplog.text
    assert "read_count=24 write_count=15 other_count=0" in caplog.text
    assert "phase_tool_count=25 phase_read_count=24 phase_write_count=1 phase_other_count=0" in caplog.text
    assert "repair_count=1" in caplog.text
    assert "remaining=1 requested_count=2" in caplog.text
    assert "src/" not in caplog.text
    assert len([c for c in gateway.calls if c["name"] in {"file_read", "file_write"}]) == 39


@pytest.mark.parametrize("repair_count,remaining,expected", [
    (0, 12, 1), (0, 13, 2), (1, 4, 1), (1, 5, 2),
])
async def test_real_repair_graph_reserves_tools_by_remaining_repair_chances(
    settings, caplog, repair_count, remaining, expected,
):
    class WritingModel(FakeModel):
        async def repair(self, artifact, context):
            self.calls.append(("repair", deepcopy(context)))
            return ModelTurn("修复", tool_calls=[ToolCall("file_write", {
                "relativeFilePath": "src/App.vue", "content": f"修复{len(self.calls)}",
            })])

    model, gateway = WritingModel(), BuildGateway()
    workflow = make_workflow(settings, model, MemoryCheckpoint(), gateway)
    graph = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"),
                                   "42:subgraph-test", {}, node="repair")
    # remaining 是首次成功写入后的余额，入口会把上一轮计数推进到本轮。
    starting_count = settings.vue_max_tool_calls - remaining - 1
    with caplog.at_level(logging.INFO):
        result = await graph.ainvoke({**initial(), "repair_count": repair_count,
                                     "tool_call_count": starting_count},
                                    {"recursion_limit": vue_recursion_limit(settings)})
    assert len(model.calls) == expected
    assert result["tool_call_count"] == starting_count + expected
    assert result["repair_count"] == repair_count + 1
    reason = "NEXT_REPAIR_RESERVE" if repair_count == 0 else "LOW_TOOL_BUDGET"
    assert f"reason={reason}" in caplog.text
    assert "vue_loop" not in result
    assert [c["vueGeneration"]["remainingToolCalls"] for _, c in model.calls] == [
        remaining + 1 - i for i in range(expected)
    ]
    assert not any(c["name"] == "project_build" for c in gateway.calls)


@pytest.mark.parametrize("case,expected", [
    ("next_repair", "finish"), ("thirteen_remaining", "model_turn"),
    ("missing_repair_count", "model_turn"), ("missing_successful_write", "model_turn"),
    ("no_write", "model_turn"), ("pending_image", "model_turn"),
    ("pending_file", "model_turn"), ("disabled", "model_turn"),
])
async def test_next_repair_reserve_keeps_eligibility_and_old_checkpoint_safety(settings, case, expected):
    state = ready_state()
    state.update(repair_count=1, tool_call_count=28)
    state["vue_loop"]["read_only_batches"] = 0
    if case == "thirteen_remaining":
        state["tool_call_count"] = 27
    if case == "missing_repair_count":
        state.pop("repair_count")
    if case == "missing_successful_write":
        state["vue_loop"].pop("successful_write")
    if case == "no_write":
        state["vue_loop"]["successful_write"] = False
    if case == "pending_image":
        state["vue_loop"]["image_used"] = False
    if case == "pending_file":
        state["vue_loop"]["file_plan"] = ["src/Required.vue"]
    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint()),
                                  EventEmitter("subgraph-test"), "42:subgraph-test", "repair",
                                  repair_budget_guard=case != "disabled")
    before = deepcopy(state)
    result = await nodes["continue_loop"](state)
    assert result["vue_loop"]["next_step"] == expected
    assert state == before
    assert set(result) == {"vue_loop"}


@pytest.mark.parametrize("tool_limit", [1, 5, 10])
async def test_custom_small_budget_yields_only_after_a_successful_write(settings, tool_limit):
    class OneWrite(FakeModel):
        async def repair(self, artifact, context):
            self.calls.append(("repair", context))
            return ModelTurn("修复", tool_calls=[ToolCall("file_write", {
                "relativeFilePath": "src/App.vue", "content": "修复",
            })])

    configured = settings.model_copy(update={"vue_max_tool_calls": tool_limit})
    model, gateway = OneWrite(), BuildGateway()
    graph = build_vue_tool_subgraph(make_workflow(configured, model, MemoryCheckpoint(), gateway),
                                   EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    result = await graph.ainvoke(initial(), {"recursion_limit": vue_recursion_limit(configured)})
    assert result["tool_call_count"] == 1
    assert len(model.calls) == 1
    assert sum(c["name"] == "file_write" for c in gateway.calls) == 1


async def test_next_repair_reserve_finishes_whole_read_write_batch(settings, caplog):
    state = ready_state()
    state.update(repair_count=1, tool_call_count=28)
    state["vue_loop"]["read_only_batches"] = 0
    state["vue_loop"]["pending_tools"].append({"name": "file_write", "arguments": {
        "relativeFilePath": "src/App.vue", "content": "同批必要写入",
    }})
    gateway = BuildGateway()
    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway),
                                  EventEmitter("subgraph-test"), "42:subgraph-test", "repair",
                                  repair_budget_guard=True)
    state.update(await nodes["continue_loop"](state))
    assert state["vue_loop"]["next_step"] == "execute_tool"
    assert not gateway.calls
    state.update(await nodes["execute_tool"](state))
    with caplog.at_level(logging.INFO):
        result = await nodes["continue_loop"](state)
    assert result["vue_loop"]["next_step"] == "finish"
    assert state["tool_call_count"] == 29
    assert [c["name"] for c in gateway.calls] == ["file_write"]
    assert "reason=NEXT_REPAIR_RESERVE" in caplog.text


async def test_checkpoint_resume_applies_next_repair_reserve_without_repeating_write(settings):
    model, gateway, checkpoint = ReadingModel(), BuildGateway(), CleaningGraphCheckpoint()
    workflow = make_workflow(settings, model, checkpoint, gateway)
    emitter = EventEmitter("subgraph-test")
    graph = build_vue_tool_subgraph(workflow, emitter, "42:subgraph-test", {}, node="repair",
                                   interrupt_after=["execute_tool"])
    graph.checkpointer = checkpoint.saver
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    await graph.ainvoke({**initial(), "tool_call_count": 27,
                         "context": {"requestId": "subgraph-test"}}, config)
    saved = await graph.aget_state(config)
    assert saved.values["tool_call_count"] == 28
    assert saved.values["repair_count"] == 1
    assert saved.values["vue_loop"]["successful_write"] is True
    rebuilt = build_vue_tool_subgraph(workflow, emitter, "42:subgraph-test", {}, node="repair")
    rebuilt.checkpointer = checkpoint.saver
    result = await rebuilt.ainvoke(None, config)
    assert result["tool_call_count"] == 28
    assert result["repair_count"] == 1
    assert len(model.calls) == 1
    assert sum(c["name"] == "file_write" for c in gateway.calls) == 1


async def test_reserve_returns_to_build_and_keeps_second_repair_and_failure(settings, caplog):
    class HighGenerationCost(ReadingModel):
        async def generate(self, branch, context):
            count = len(context["toolResults"])
            if count >= 28:
                return ModelTurn("生成完成")
            return ModelTurn("写入", tool_calls=[ToolCall("file_write", {
                "relativeFilePath": f"src/File{i}.vue", "content": "页面",
            }) for i in range(count, count + 2)])

    model, gateway = HighGenerationCost(), BuildGateway(fixed=False)
    with caplog.at_level(logging.INFO):
        events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == "failed"
    assert events[-1].error.code == "VUE_COMPONENT_CONTRACT_FAILED"
    assert sum(c["name"] == "project_build" for c in gateway.calls) == 3
    repairs = [context for name, context in model.calls if name == "repair"]
    assert [context["repairCount"] for context in repairs] == [1, 2, 2, 2, 2]
    assert [context["vueGeneration"]["remainingToolCalls"] for context in repairs] == [12, 11, 10, 8, 6]
    assert "reason=NEXT_REPAIR_RESERVE" in caplog.text
    assert "reason=LOW_TOOL_BUDGET" in caplog.text
    assert sum(e.type in {"completed", "failed"} for e in events) == 1
