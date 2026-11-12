"""重复读取只能交回验证，不能替代真实构建或宣告修复成功。"""

from copy import deepcopy

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.vue_read_stall import repair_reads_stalled
from ai_service.orchestration.vue_tool_loop import create_vue_loop_nodes, initial_progress
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint
from test_subgraphs import request_for
from test_vue_subgraph import make_workflow


PREFIX = "request:vue-repair:1"


def record(index, *, tool="file_read", path="src/App.vue", result=None, prefix=PREFIX):
    return {"toolCallId": f"{prefix}:{index}", "tool": tool,
            "arguments": {"relativeFilePath": path},
            "result": {"content": "完整源码"} if result is None else result}


def repeated_history():
    return [record(1, tool="file_write", result={"ok": True}),
            *[record(i) for i in range(2, 7)]]


def test_identical_reads_require_write_and_four_consecutive_repeats_without_mutating_history():
    history = repeated_history()
    before = deepcopy(history)
    assert repair_reads_stalled(history, PREFIX)
    assert not repair_reads_stalled(history[:-1], PREFIX)
    assert not repair_reads_stalled(history[1:], PREFIX)
    assert not repair_reads_stalled(history, "request:vue-repair:2")
    assert not repair_reads_stalled(history, "request:vue-repair")
    assert history == before


@pytest.mark.parametrize("change", [
    {"path": "src/New.vue"},
    {"result": {"content": "新的完整源码"}},
    {"result": {"content": "完整源码", "truncated": True}},
    {"result": {"content": "完整源码", "ok": False}},
    {"result": {"content": "完整源码", "ok": "true"}},
    {"result": {"contextOmitted": True}},
    {"result": {"content": "x" * 12001}},
    {"result": {"content": None}},
    {"tool": "dir_read"},
    {"tool": "image_search"},
    {"tool": "file_delete", "result": {"ok": True}},
    {"tool": "file_modify", "result": {"ok": True}},
    {"tool": "file_modify", "result": {"ok": False}},
])
def test_new_information_or_uncertain_results_reset_the_streak(change):
    history = repeated_history()
    history.append(record(7, **change))
    assert not repair_reads_stalled(history, PREFIX)


def test_write_requires_explicit_success_and_old_phase_does_not_count():
    for result in ({}, {"ok": False}, {"ok": 1}):
        history = repeated_history()
        history[0]["result"] = result
        assert not repair_reads_stalled(history, PREFIX)
    history = repeated_history()
    history[0]["toolCallId"] = "request:vue-generate:1"
    assert not repair_reads_stalled(history, PREFIX)


def test_alternating_paths_repeat_but_new_content_resets_count():
    history = [record(1, tool="file_modify", result={"ok": True})]
    for i in range(2, 8):
        history.append(record(i, path="src/A.vue" if i % 2 else "src/B.vue"))
    assert repair_reads_stalled(history, PREFIX)
    history.append(record(8, path="src/B.vue", result={"content": "变化"}))
    assert not repair_reads_stalled(history, PREFIX)


@pytest.mark.parametrize("case", ["enabled", "generation", "pending_write", "mixed_batch",
                                   "pending_file", "pending_image"])
async def test_only_finished_read_batch_of_eligible_repair_can_yield(settings, case):
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint())
    loop = initial_progress({}, PREFIX, "2026-11-12T00:00:00+00:00")
    read = {"name": "file_read", "arguments": {"relativeFilePath": "src/App.vue"}}
    write = {"name": "file_write", "arguments": {"relativeFilePath": "src/App.vue", "content": "x"}}
    loop.update(pending_tools=[read], tool_cursor=1)
    if case == "pending_write":
        loop.update(pending_tools=[read, write], tool_cursor=1)
    if case == "mixed_batch":
        loop.update(pending_tools=[write, read], tool_cursor=2)
    if case == "pending_file":
        loop["file_plan"] = ["src/Unwritten.vue"]
    if case == "pending_image":
        loop["image_used"] = False
    nodes = create_vue_loop_nodes(workflow, EventEmitter("request"), "42:request", "repair",
                                  repair_read_guard=case != "generation")
    state = {"request_id": "request", "vue_loop": loop, "vue_tool_results": repeated_history()}
    result = await nodes["continue_loop"](state)
    expected = "finish" if case == "enabled" else ("execute_tool" if case == "pending_write" else "model_turn")
    assert result["vue_loop"]["next_step"] == expected


async def test_cancellation_precedes_repeated_read_exit(settings):
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint())
    workflow.cancellations.cancel("42:request")
    nodes = create_vue_loop_nodes(workflow, EventEmitter("request"), "42:request", "repair",
                                  repair_read_guard=True)
    # 取消必须先于读取状态或决定交回验证，故这里无需提供其余状态。
    with pytest.raises(GenerationCancelled):
        await nodes["continue_loop"]({})


@pytest.mark.parametrize("fixed", [True, False])
async def test_repair_repeated_reads_return_to_build_before_tool_budget_exhaustion(settings, fixed):
    class Model(FakeModel):
        async def repair(self, artifact, context):
            self.calls.append(("repair", context))
            results = [r for r in context["toolResults"]
                       if r["toolCallId"].startswith(f"{context['requestId']}:vue-repair:{context['repairCount']}:")]
            if not results:
                return ModelTurn("修复", tool_calls=[ToolCall("file_write", {
                    "relativeFilePath": "src/App.vue", "content": "测试源码",
                })])
            return ModelTurn("确认", tool_calls=[ToolCall("file_read", {
                "relativeFilePath": path,
            }) for path in ("src/App.vue", "src/Filter.vue")])

    class Gateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            if name == "project_build":
                count = sum(c["name"] == name for c in self.calls)
                if count == 1 or not fixed:
                    return {"built": False, "errorCode": "VUE_COMPONENT_CONTRACT_FAILED",
                            "message": "组件缺少属性"}
            if name == "file_read":
                return {"content": "同一份完整测试源码"}
            return result

    model, gateway = Model(), Gateway()
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == ("completed" if fixed else "failed")
    if not fixed:
        assert events[-1].error.code == "VUE_COMPONENT_CONTRACT_FAILED"
    builds = [c for c in gateway.calls if c["name"] == "project_build"]
    assert len(builds) == (2 if fixed else 3)
    assert len({c["toolCallId"] for c in builds}) == len(builds)
    repairs = [c for c in gateway.calls if ":vue-repair:" in c["toolCallId"]]
    assert len(repairs) == (7 if fixed else 14)
    assert len([c for c in model.calls if c[0] == "review"]) == (1 if fixed else 0)
    assert sum(e.type in {"completed", "failed"} for e in events) == 1
