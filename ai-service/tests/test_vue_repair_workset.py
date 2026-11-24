"""严格问题任务限制修复工作集；完成声明仍须回到原构建与Reviewer。"""

from copy import deepcopy
from dataclasses import asdict
import json

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.models.openai_compatible import _vue_model_turn
from ai_service.models.vue_context import project_vue_context
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph
from ai_service.orchestration.vue_tool_loop import create_vue_loop_nodes, initial_progress
from conftest import FakeModel, MemoryCheckpoint, FakeToolGateway
from test_vue_context import record
from test_vue_prewrite_guard import Gateway
from test_vue_subgraph import initial, make_workflow
from ai_service.orchestration.vue_repair_workset import (
    initial_repair_work, prepare_work_turn, record_work_result, finish_work_batch, require_write_after_reads,
)


FILES = ["src/App.vue", "src/Composer.vue", "src/Hero.vue", "src/data.js", "src/Card.vue", "src/Timeline.vue"]
ISSUES = [{"code": "FUNCTIONAL_ERROR", "summary": summary, "evidence": "有限证据", "repair_hint": "核对调用链"}
          for summary in ("保存接线", "打开表单", "记录字段")]


class TaskModel(FakeModel):
    async def repair(self, artifact, context):
        self.calls.append(("repair", deepcopy(context)))
        work = context["repairWork"]
        pending = next((i for i in range(len(ISSUES)) if i not in work["completedIssueIndexes"]), None)
        if pending is None:
            return ModelTurn("交回原验证")
        active = work["activePlan"]
        if not active:
            paths = ([FILES[0], FILES[1]], [FILES[0], FILES[2]], [FILES[3], FILES[4]])[pending]
            return ModelTurn("仅读当前问题", repair_plan={"issueIndex": pending, "files": paths},
                             tool_calls=[ToolCall("file_read", {"relativeFilePath": path}) for path in paths])
        if not work["activeWriteSucceeded"]:
            return ModelTurn("最小修改", tool_calls=[ToolCall("file_write", {
                "relativeFilePath": active["files"][0], "content": f"修复问题{pending}",
            })])
        return ModelTurn("该项交回待验证", completed_issue_index=pending)


async def test_three_issues_use_small_worksets_without_reading_all_six_files(settings):
    model, gateway = TaskModel(), Gateway()
    checkpoint = MemoryCheckpoint()
    graph = build_vue_tool_subgraph(make_workflow(settings, model, checkpoint, gateway),
                                   EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    result = await graph.ainvoke({**initial(), "repair_issues": ISSUES, "repair_file_manifest": FILES},
                                {"recursion_limit": 300})
    assert result["tool_call_count"] == 9 and result["repair_count"] == 1
    assert sum(call["name"] == "file_write" for call in gateway.calls) == 3
    assert "repairWork" not in result["context"]
    assert all("verification_history" not in str(context) for _, context in model.calls)
    assert all(len(context["repairWork"]["activePlan"].get("files", [])) <= 3 for _, context in model.calls)


@pytest.mark.parametrize("plan", [
    {"issueIndex": True, "files": FILES[:2]}, {"issueIndex": 3, "files": FILES[:2]},
    {"issueIndex": 1, "files": FILES[:2]}, {"issueIndex": 0, "files": FILES[:4]},
    {"issueIndex": 0, "files": ["../private"]}, {"issueIndex": 0, "files": ["C:/private"]},
    {"issueIndex": 0, "files": ["src/Missing.vue"]}, {"issueIndex": 0, "files": [FILES[0], FILES[0]]},
    {"issueIndex": 0, "files": FILES[:2], "extra": "private"},
])
async def test_invalid_plan_never_executes_first_tool_and_has_bounded_retries(settings, plan):
    class Invalid(TaskModel):
        async def repair(self, artifact, context):
            return ModelTurn("private", repair_plan=plan, tool_calls=[ToolCall("file_write", {
                "relativeFilePath": FILES[0], "content": "bad",
            })])
    gateway = Gateway()
    graph = build_vue_tool_subgraph(make_workflow(settings, Invalid(), MemoryCheckpoint(), gateway),
                                   EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    with pytest.raises(ValueError, match="VUE_REPAIR_PLAN_INVALID"):
        await graph.ainvoke({**initial(), "repair_issues": ISSUES, "repair_file_manifest": FILES},
                            {"recursion_limit": 100})
    assert not gateway.calls


@pytest.mark.parametrize("payload", [
    {"repairPlan": {"issueIndex": True, "files": FILES[:1]}},
    {"repairPlan": {"issueIndex": 0, "files": FILES[:4]}},
    {"completedIssueIndex": True}, {"completedIssueIndex": -1},
])
def test_adapter_rejects_malformed_work_protocol(payload):
    from types import SimpleNamespace
    response = SimpleNamespace(content=json.dumps({"content": "", "toolCalls": [], **payload}),
                               response_metadata={}, usage_metadata={})
    assert _vue_model_turn(response).vue_output_invalid


def test_workset_pins_complete_files_or_fails_instead_of_rotating_them():
    context = {"repairWork": {"activePlan": {"issueIndex": 0, "files": ["src/0.vue", "src/1.vue"]}},
               "toolResults": [record(i, "x" * 6000) for i in range(3)]}
    projected = project_vue_context(context, fits=lambda _: True)
    assert all("content" in projected["toolResults"][i]["result"] for i in (0, 1))
    # 活动工作集完整文件放不下时明确失败；不能轮流省略再要求补读。
    context["toolResults"][0]["result"]["content"] = "中" * 5000
    context["toolResults"][1]["result"]["content"] = "中" * 5000
    with pytest.raises(ValueError, match="VUE_CONTEXT_BUDGET_EXCEEDED"):
        project_vue_context(context, fits=lambda _: True)


async def test_full_workflow_builds_and_reviews_again_after_all_issue_claims(settings):
    from ai_service.models.single_quality_review import SingleReviewResult
    from test_subgraphs import request_for
    class SourceGateway(FakeToolGateway):
        def __init__(self):
            super().__init__()
            self.sources = {path: "初始源码" for path in FILES}
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            if name == "file_write":
                self.sources[arguments["relativeFilePath"]] = arguments["content"]
                return {"ok": True}
            if name == "file_read":
                return {"content": self.sources[arguments["relativeFilePath"]]}
            if name == "vue_source_snapshot":
                return {"files": [{"path": path, "content": content, "truncated": False}
                                  for path, content in self.sources.items()], "eligibleFileCount": 6,
                        "includedFileCount": 6, "omittedFileCount": 0, "truncated": False}
            return result
    model = TaskModel(reviews=[SingleReviewResult(verdict="REPAIR", issues=ISSUES), True])
    gateway, checkpoint = SourceGateway(), MemoryCheckpoint()
    events = await make_workflow(settings, model, checkpoint, gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == "completed"
    assert sum(e.type in {"completed", "failed"} for e in events) == 1
    assert sum(c["name"] == "project_build" for c in gateway.calls) == 2
    assert sum(name == "review" for name, _ in model.calls) == 2
    assert checkpoint.saved["42:subgraph-test"][-1]["repairCount"] == 1
    assert checkpoint.saved["42:subgraph-test"][-1]["toolCallCount"] == 9
    assert "repair_issues" not in str(checkpoint.saved)
    assert "repairWork" not in str([e.model_dump() for e in events])


@pytest.mark.parametrize("change", [
    {"completed_issue_index": 0}, {"content": "全部完成"},
    {"tool_calls": [{"name": "file_read", "arguments": {"relativeFilePath": FILES[3]}}]},
    {"tool_calls": [{"name": "dir_read", "arguments": {"relativeDirPath": "src"}}]},
    {"repair_plan": {"issueIndex": 0, "files": FILES[2:4]}},
])
def test_unwritten_work_cannot_claim_complete_switch_or_read_outside(change):
    work = initial_repair_work(ISSUES, FILES)
    plan = {"issueIndex": 0, "files": FILES[:2]}
    work, action = prepare_work_turn(work, asdict(ModelTurn("计划", repair_plan=plan)))
    assert action == "continue"
    before = deepcopy(work)
    with pytest.raises(ValueError, match="VUE_REPAIR_PLAN_INVALID"):
        prepare_work_turn(work, {**asdict(ModelTurn("")), **change})
    assert work == before


@pytest.mark.parametrize("result", [{"ok": False}, {}, {"unknown": True}])
def test_unknown_or_failed_write_does_not_allow_completion(result):
    work = initial_repair_work(ISSUES, FILES)
    work, _ = prepare_work_turn(work, asdict(ModelTurn("", repair_plan={"issueIndex": 0, "files": FILES[:1]},
                                                     tool_calls=[ToolCall("file_write", {
                                                         "relativeFilePath": FILES[0], "content": "new",
                                                     })])))
    work = record_work_result(work, {"tool": "file_write", "result": result})
    with pytest.raises(ValueError, match="VUE_REPAIR_PLAN_INVALID"):
        prepare_work_turn(work, asdict(ModelTurn("", completed_issue_index=0)))


async def test_repeated_pure_reads_must_write_and_do_not_execute_fourth_batch(settings):
    class Reader(TaskModel):
        async def repair(self, artifact, context):
            return ModelTurn("读取", repair_plan={"issueIndex": 0, "files": FILES[:2]},
                             tool_calls=[ToolCall("file_read", {"relativeFilePath": FILES[0]})])
    gateway, checkpoint = Gateway(), MemoryCheckpoint()
    graph = build_vue_tool_subgraph(make_workflow(settings, Reader(), checkpoint, gateway),
                                   EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    with pytest.raises(ValueError, match="VUE_REPAIR_WORKSET_STALLED"):
        await graph.ainvoke({**initial(), "repair_issues": ISSUES, "repair_file_manifest": FILES})
    assert sum(c["name"] == "file_read" for c in gateway.calls) == 3
    assert checkpoint.saved["42:subgraph-test"][-1]["verification_history"][-1]["toolCount"] == 3


async def test_rebuild_resumes_workset_and_pending_batch_without_replanning(settings):
    from test_subgraphs import CleaningGraphCheckpoint
    checkpoint, model, gateway = CleaningGraphCheckpoint(), TaskModel(), Gateway()
    workflow = make_workflow(settings, model, checkpoint, gateway)
    graph = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {},
                                   node="repair", interrupt_after=["execute_tool"])
    graph.checkpointer = checkpoint.saver
    config = {"configurable": {"thread_id": "42:subgraph-test"}, "recursion_limit": 300}
    await graph.ainvoke({**initial(), "repair_issues": ISSUES, "repair_file_manifest": FILES}, config)
    saved = await graph.aget_state(config)
    assert saved.values["vue_loop"]["repair_work"]["active"]["files"] == FILES[:2]
    rebuilt = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    rebuilt.checkpointer = checkpoint.saver
    result = await rebuilt.ainvoke(None, config)
    assert result["tool_call_count"] == 9
    assert len([c for c in gateway.calls if c["name"] in {"file_read", "file_write"}]) == 9
    assert len({c["toolCallId"] for c in gateway.calls}) == len(gateway.calls)


def test_complete_workset_fails_actual_message_budget_without_partial_source():
    context = {"repairWork": {"activePlan": {"issueIndex": 0, "files": ["src/1.vue", "src/2.vue"]}},
               "toolResults": [record(i, "x" * 100) for i in (1, 2)]}
    with pytest.raises(ValueError, match="VUE_CONTEXT_BUDGET_EXCEEDED"):
        project_vue_context(context, fits=lambda ctx: sum("content" in t["result"] for t in ctx["toolResults"]) <= 1)


@pytest.mark.parametrize("paths", [["../bad"], ["C:/bad"], ["src\\bad.vue"], ["src/a.vue", "src/a.vue"], ["/etc/passwd"]])
def test_snapshot_manifest_does_not_trust_arbitrary_paths(paths):
    from ai_service.models.vue_repair import snapshot_file_manifest
    assert snapshot_file_manifest({"files": [{"path": path} for path in paths]}) == []


async def test_cancel_before_workset_validation_never_executes_tools(settings, monkeypatch):
    import asyncio
    workflow = make_workflow(settings, TaskModel(), MemoryCheckpoint(), Gateway())
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", "repair",
                                  repair_read_guard=True)
    loop = initial_progress({}, "subgraph-test:vue-repair:1", "2026-11-24T00:00:00+00:00")
    loop.update(repair_work=initial_repair_work(ISSUES, FILES), turn=asdict(ModelTurn("假完成")))
    def cancel(_):
        raise asyncio.CancelledError
    monkeypatch.setattr(workflow, "_raise_if_cancelled", cancel)
    with pytest.raises(asyncio.CancelledError):
        await nodes["validate_turn"]({**initial(), "vue_loop": loop, "vue_tool_results": []})
    assert not workflow.tool_gateway.calls


async def test_adapter_sends_work_protocol_only_for_enabled_repair():
    from test_openai_compatible import model_with_response
    model = model_with_response('{"content":"","repairPlan":{"issueIndex":0,"files":["src/App.vue"]},"toolCalls":[]}')
    turn = await model.repair("", {"codeGenType": "VUE_PROJECT", "repairWork": {"activePlan": {}}})
    assert turn.repair_plan == {"issueIndex": 0, "files": ["src/App.vue"]}
    assert "completedIssueIndex" in model._client.messages[0].content
    await model.repair("", {"codeGenType": "VUE_PROJECT"})
    assert "completedIssueIndex" not in model._client.messages[0].content
