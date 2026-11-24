"""计划拒绝只保存固定原因和形状；不从留存计数倒推历史模型正文。"""

from copy import deepcopy
from dataclasses import asdict
import os

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.vue_repair_workset import (
    initial_repair_work, prepare_work_turn, require_write_after_reads,
)
from test_vue_repair_workset import FILES, ISSUES


def turn(**changes):
    return {**asdict(ModelTurn("敏感正文哨兵")), **changes}


def active_work(*, written=False):
    work = initial_repair_work(ISSUES, FILES)
    work.update(active={"issueIndex": 0, "files": FILES[:2]}, active_write=written)
    return work


@pytest.mark.parametrize("reason,work,response", [
    ("FILE_PLAN_NOT_ALLOWED", active_work(), turn(file_plan=FILES)),
    ("COMPLETION_OUT_OF_ORDER", active_work(), turn(completed_issue_index=1)),
    ("COMPLETION_WITHOUT_PLAN", initial_repair_work(ISSUES, FILES), turn(completed_issue_index=0)),
    ("COMPLETION_BEFORE_WRITE", active_work(), turn(completed_issue_index=0)),
    ("COMPLETION_WITH_TOOLS", active_work(written=True), turn(completed_issue_index=0,
        tool_calls=[asdict(ToolCall("file_read", {"relativeFilePath": FILES[0]}))])),
    ("COMPLETION_WITH_PLAN", active_work(written=True), turn(completed_issue_index=0,
        repair_plan={"issueIndex": 0, "files": FILES[:1]})),
    ("WORK_ALREADY_COMPLETED", {**active_work(), "completed": [0, 1, 2]}, turn(repair_plan={"issueIndex": 0, "files": FILES[:1]})),
    ("PLAN_SCHEMA_INVALID", active_work(), turn(repair_plan={"files": ["敏感路径"]})),
    ("ISSUE_OUT_OF_ORDER", active_work(), turn(repair_plan={"issueIndex": 1, "files": FILES[:1]})),
    ("PLAN_OUTSIDE_MANIFEST", active_work(), turn(repair_plan={"issueIndex": 0, "files": ["private/secret.vue"]})),
    ("WORKSET_SWITCH_BEFORE_WRITE", active_work(), turn(repair_plan={"issueIndex": 0, "files": FILES[2:3]})),
    ("PLAN_REQUIRED", initial_repair_work(ISSUES, FILES), turn()),
    ("EMPTY_TURN", active_work(), turn()),
    ("PLANNING_ONLY_REPEATED", {**active_work(), "planning_only": True}, turn(repair_plan={"issueIndex": 0, "files": FILES[:2]})),
    ("TOOL_NOT_ALLOWED", active_work(), turn(tool_calls=[asdict(ToolCall("dir_read", {"relativeDirPath": "private"}))])),
    ("TOOL_OUTSIDE_WORKSET", active_work(), turn(tool_calls=[asdict(ToolCall("file_read", {"relativeFilePath": FILES[3]}))])),
])
def test_rejection_has_fixed_first_reason_without_mutation_or_text(reason, work, response):
    before = deepcopy(work)
    with pytest.raises(ValueError, match="VUE_REPAIR_PLAN_INVALID") as failure:
        prepare_work_turn(work, response)
    assert failure.value.reason == reason
    assert str(failure.value) == "VUE_REPAIR_PLAN_INVALID: 修复计划或完成声明不符合当前问题及文件范围"
    assert "敏感" not in str(failure.value) and "private" not in str(failure.value)
    assert work == before


def test_stalled_reads_have_separate_reason_and_keep_original_public_code():
    work = {**active_work(), "read_batches": 3}
    calls = [asdict(ToolCall("file_read", {"relativeFilePath": FILES[0]}))]
    with pytest.raises(ValueError, match="VUE_REPAIR_WORKSET_STALLED") as failure:
        require_write_after_reads(work, calls)
    assert failure.value.reason == "WRITE_REQUIRED_AFTER_READS"


def test_first_violation_precedence_is_stable():
    with pytest.raises(ValueError) as failure:
        prepare_work_turn(active_work(), turn(file_plan=FILES, completed_issue_index=1))
    assert failure.value.reason == "FILE_PLAN_NOT_ALLOWED"


@pytest.mark.parametrize("paths", [FILES[:3], FILES[:1], [FILES[0], FILES[2]], list(reversed(FILES[:2]))])
def test_real_observed_replacement_shapes_replay_without_reconstructing_original_paths(paths):
    from types import SimpleNamespace
    import json
    from ai_service.models.openai_compatible import _vue_model_turn
    # 新实测仅保留2文件活动集及3/1/2文件请求形状；末次换成员或换序无法区分，两种都覆盖。
    payload = {"content": "匿名形状回放", "repairPlan": {"issueIndex": 0, "files": paths},
               "toolCalls": [{"name": "file_read", "arguments": {"relativeFilePath": paths[0]}}]}
    parsed = _vue_model_turn(SimpleNamespace(content=json.dumps(payload), response_metadata={}, usage_metadata={}))
    assert not parsed.vue_output_invalid
    with pytest.raises(ValueError) as error:
        prepare_work_turn(active_work(), asdict(parsed))
    assert error.value.reason == "WORKSET_SWITCH_BEFORE_WRITE"


def test_omitting_plan_keeps_active_set_and_allows_original_write_and_claim_protocol():
    work = active_work()
    call = asdict(ToolCall("file_write", {"relativeFilePath": FILES[0], "content": "匿名最小修改"}))
    work, action = prepare_work_turn(work, turn(tool_calls=[call]))
    assert action == "execute" and work["active"]["files"] == FILES[:2]
    from ai_service.orchestration.vue_repair_workset import record_work_result
    work = record_work_result(work, {"tool": "file_write", "result": {"ok": True}})
    work, action = prepare_work_turn(work, turn(completed_issue_index=0))
    assert action == "continue" and work["completed"] == [0]


def diagnostic(reason="COMPLETION_WITH_TOOLS"):
    from ai_service.models.vue_plan_diagnostics import plan_rejection_snapshot
    return plan_rejection_snapshot(reason, active_work(written=True), turn(
        completed_issue_index=0, tool_calls=[asdict(ToolCall("file_read", {"relativeFilePath": FILES[0]}))]), 2)


def history_record():
    return {"round": 1, "stage": "repair", "verdict": "ERROR", "errorCode": "VUE_REPAIR_PLAN_INVALID",
            "toolCount": 3, "phaseReadCount": 2, "phaseWriteCount": 1, "repeatedReadCount": 0,
            "visibleRepeatCount": 0, "omittedRereadCount": 0, "issueCount": 0, "issueCodes": [],
            "planRejection": diagnostic()}


@pytest.mark.parametrize("change", [
    {"reason": "敏感哨兵"}, {"reason": "WRITE_REQUIRED_AFTER_READS"}, {"path": "private/secret"},
    {"hasPlan": 1}, {"hasCompletion": "yes"}, {"formatRetryCount": 3}, {"requestedToolCount": True},
    {"activeFileCount": 4}, {"planFileCount": 41}, {"issueCount": 0}, {"issueCount": 13},
    {"completedIssueCount": 4}, {"readBatchCount": -1},
])
def test_both_white_lists_reject_unknown_or_mismatched_shape(change):
    from ai_service.models.verification_diagnostics import safe_verification_history
    from ai_service.orchestration.workflow import GenerationWorkflow
    from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint
    record = history_record()
    record["planRejection"].update(change)
    assert safe_verification_history([record]) is None
    base = {"app_id": "42", "request_id": "req", "code_gen_type": "VUE_PROJECT"}
    assert "verification_history" not in GenerationWorkflow._checkpoint_payload({**base, "verification_history": [record]}, "repair")
    payload = GenerationWorkflow._checkpoint_payload(base, "repair")
    payload["verification_history"] = [record]
    assert "verification_history" not in PostgresCheckpoint._normalize_state(payload)


def test_old_history_compatible_and_stalled_code_requires_matching_reason():
    from ai_service.models.verification_diagnostics import safe_verification_history
    record = history_record()
    assert safe_verification_history([record]) == [record]
    old = {k: v for k, v in record.items() if k != "planRejection"}
    assert safe_verification_history([old]) == [old]
    record["errorCode"] = "VUE_REPAIR_WORKSET_STALLED"
    assert safe_verification_history([record]) is None
    record["planRejection"]["reason"] = "WRITE_REQUIRED_AFTER_READS"
    assert safe_verification_history([record]) == [record]
    record["errorCode"] = "VUE_MODEL_INVALID_OUTPUT"
    assert safe_verification_history([record]) is None


def make_nodes(settings, *, retries=0, work=None, response=None, checkpoint=None):
    from ai_service.orchestration.events import EventEmitter
    from ai_service.orchestration.vue_tool_loop import initial_progress, create_vue_loop_nodes
    from conftest import FakeModel, MemoryCheckpoint
    from test_vue_subgraph import initial, make_workflow
    workflow = make_workflow(settings, FakeModel(), checkpoint or MemoryCheckpoint())
    loop = initial_progress({}, "subgraph-test:vue-repair:1", "2026-11-24T00:00:00+00:00")
    loop.update(format_retries=retries, repair_work=work or active_work(written=True),
                turn=response or turn(completed_issue_index=0, tool_calls=[asdict(ToolCall(
                    "file_write", {"relativeFilePath": FILES[0], "content": "敏感源码哨兵"}))]))
    state = {**initial(), "repair_count": 1, "vue_loop": loop, "vue_tool_results": []}
    emitter = EventEmitter("subgraph-test")
    nodes = create_vue_loop_nodes(workflow, emitter, "42:subgraph-test", "repair", repair_read_guard=True)
    return workflow, nodes, state, emitter


async def test_each_rejection_logged_and_last_attempt_saved_without_tools(settings, caplog):
    workflow, nodes, state, _ = make_nodes(settings)
    for retry in range(3):
        state["vue_loop"]["turn"] = turn(completed_issue_index=0, tool_calls=[asdict(ToolCall(
            "file_write", {"relativeFilePath": FILES[0], "content": "敏感源码哨兵"}))])
        if retry < 2:
            result = await nodes["validate_turn"](state)
            state.update(result)
            assert state["vue_loop"]["plan_rejection"]["formatRetryCount"] == retry
        else:
            with pytest.raises(ValueError, match="VUE_REPAIR_PLAN_INVALID"):
                await nodes["validate_turn"](state)
    saved = workflow.checkpoint.saved["42:subgraph-test"][-1]["verification_history"][-1]
    assert saved["planRejection"]["reason"] == "COMPLETION_WITH_TOOLS"
    assert saved["planRejection"]["formatRetryCount"] == 2
    assert not workflow.tool_gateway.calls
    assert caplog.text.count("Vue repair plan rejected") == 3
    assert "敏感" not in caplog.text and "relativeFilePath" not in caplog.text
    assert "敏感" not in str(saved) and "src/" not in str(saved)


async def test_accepted_turn_clears_private_rejection_and_shared_retry_is_not_reset(settings):
    _, nodes, state, _ = make_nodes(settings, retries=1)
    state.update(await nodes["validate_turn"](state))
    assert state["vue_loop"]["format_retries"] == 2
    state["vue_loop"]["turn"] = turn(tool_calls=[asdict(ToolCall("file_read", {"relativeFilePath": FILES[0]}))])
    state.update(await nodes["validate_turn"](state))
    assert state["vue_loop"]["plan_rejection"] == {}
    assert state["vue_loop"]["format_retries"] == 2
    state["vue_loop"]["turn"] = turn(vue_output_invalid=True)
    with pytest.raises(ValueError, match="VUE_MODEL_INVALID_OUTPUT"):
        await nodes["validate_turn"](state)


@pytest.mark.parametrize("failure", ["log", "save", "cancel"])
async def test_observation_failures_preserve_error_or_propagate_cancel(settings, monkeypatch, failure):
    import asyncio
    from ai_service.orchestration import vue_tool_loop
    workflow, nodes, state, _ = make_nodes(settings, retries=2)
    def bad_log(*args, **kwargs):
        raise RuntimeError("敏感日志异常")
    async def bad_save(*args, **kwargs):
        if failure == "cancel":
            raise asyncio.CancelledError
        raise RuntimeError("敏感保存异常")
    if failure == "log":
        monkeypatch.setattr(vue_tool_loop.logger, "warning", bad_log)
    else:
        monkeypatch.setattr(workflow.checkpoint, "save", bad_save)
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else ValueError) as error:
        await nodes["validate_turn"](state)
    if failure != "cancel":
        assert "VUE_REPAIR_PLAN_INVALID" in str(error.value)
    assert not workflow.tool_gateway.calls


async def test_checkpoint_resume_keeps_retries_and_last_rejection_private(settings):
    from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph
    from ai_service.orchestration.events import EventEmitter
    from conftest import FakeModel
    from test_subgraphs import CleaningGraphCheckpoint
    from test_vue_subgraph import initial, make_workflow
    class Invalid(FakeModel):
        async def repair(self, artifact, context):
            self.calls.append(("repair", deepcopy(context)))
            return ModelTurn("敏感模型正文", completed_issue_index=0)
    model, checkpoint = Invalid(), CleaningGraphCheckpoint()
    workflow = make_workflow(settings, model, checkpoint)
    graph = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {},
                                   node="repair", interrupt_after=["validate_turn"])
    graph.checkpointer = checkpoint.saver
    config = {"configurable": {"thread_id": "42:subgraph-test"}, "recursion_limit": 100}
    await graph.ainvoke({**initial(), "repair_issues": ISSUES, "repair_file_manifest": FILES}, config)
    saved = await graph.aget_state(config)
    assert saved.values["vue_loop"]["plan_rejection"]["reason"] == "COMPLETION_WITHOUT_PLAN"
    assert saved.values["vue_loop"]["format_retries"] == 1
    rebuilt = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    rebuilt.checkpointer = checkpoint.saver
    with pytest.raises(ValueError, match="VUE_REPAIR_PLAN_INVALID"):
        await rebuilt.ainvoke(None, config)
    assert len(model.calls) == 3
    assert "plan_rejection" not in str(model.calls) and "COMPLETION_WITHOUT_PLAN" not in str(model.calls)
    assert not workflow.tool_gateway.calls


@pytest.mark.parametrize("reason", [
    "FILE_PLAN_NOT_ALLOWED", "COMPLETION_OUT_OF_ORDER", "COMPLETION_WITHOUT_PLAN", "COMPLETION_BEFORE_WRITE",
    "COMPLETION_WITH_TOOLS", "COMPLETION_WITH_PLAN", "WORK_ALREADY_COMPLETED", "PLAN_SCHEMA_INVALID",
    "ISSUE_OUT_OF_ORDER", "PLAN_OUTSIDE_MANIFEST", "WORKSET_SWITCH_BEFORE_WRITE", "PLAN_REQUIRED",
    "EMPTY_TURN", "PLANNING_ONLY_REPEATED", "TOOL_NOT_ALLOWED", "TOOL_OUTSIDE_WORKSET",
])
def test_every_plan_reason_survives_both_projection_boundaries(reason):
    from ai_service.orchestration.workflow import GenerationWorkflow
    from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint
    record = history_record()
    record["planRejection"]["reason"] = reason
    base = {"app_id": "42", "request_id": "req", "code_gen_type": "VUE_PROJECT", "verification_history": [record]}
    payload = GenerationWorkflow._checkpoint_payload(base, "repair")
    assert PostgresCheckpoint._normalize_state(payload)["verification_history"] == [record]


async def test_full_workflow_has_one_failed_terminal_without_diagnostic_events(settings):
    from conftest import FakeModel, MemoryCheckpoint, ChangingSnapshotGateway
    from ai_service.models.single_quality_review import SingleReviewResult
    from test_subgraphs import request_for
    from test_vue_subgraph import make_workflow
    class Invalid(FakeModel):
        async def repair(self, artifact, context):
            self.calls.append(("repair", deepcopy(context)))
            return ModelTurn("不可输出的拒绝正文", completed_issue_index=0)
    model = Invalid(reviews=[SingleReviewResult(verdict="REPAIR", issues=ISSUES)])
    checkpoint = MemoryCheckpoint()
    events = await make_workflow(settings, model, checkpoint, ChangingSnapshotGateway()).run(request_for("VUE_PROJECT"))
    assert events[-1].error.code == "VUE_REPAIR_PLAN_INVALID"
    assert sum(e.type in {"failed", "completed"} for e in events) == 1
    public = str([e.model_dump() for e in events])
    assert "planRejection" not in public and "COMPLETION_WITHOUT_PLAN" not in public
    assert "不可输出" not in public
    assert "COMPLETION_WITHOUT_PLAN" not in str(model.calls)
    record = checkpoint.saved["42:subgraph-test"][-1]["verification_history"][-1]
    assert record["planRejection"]["reason"] == "COMPLETION_WITHOUT_PLAN"


@pytest.mark.skipif(os.getenv("AI_SERVICE_POSTGRES_INTEGRATION") != "true", reason="需显式开启真实PostgreSQL")
async def test_real_postgres_preserves_plan_diagnostic_when_following_summary_omits_it():
    from uuid import uuid4
    from psycopg import AsyncConnection
    from psycopg.types.json import Jsonb
    from ai_service.config import Settings
    from ai_service.infrastructure.postgres_checkpoint import STATUS_UPSERT_SQL
    request_id = uuid4().hex
    thread_id = f"plan-diagnostic:{request_id}"
    connection = await AsyncConnection.connect(Settings().checkpoint_postgres_url)
    try:
        for payload in ({"verification_history": [history_record()]}, {"node": "repair"}):
            await connection.execute(STATUS_UPSERT_SQL, (
                thread_id, 42, request_id, "VUE_PROJECT", "repair", None, 1, 3, Jsonb(payload), 60,
            ))
            cursor = await connection.execute("SELECT status_payload FROM ai_workflow_status WHERE thread_id=%s", (thread_id,))
            row = await cursor.fetchone()
            assert row[0]["verification_history"] == [history_record()]
    finally:
        # 使用唯一测试行并回滚，不能修改真实业务记录或触发全局清理。
        await connection.rollback()
        await connection.close()


async def test_targeted_feedback_keeps_active_plan_then_writes_and_returns_to_original_validation(settings):
    from conftest import FakeModel, MemoryCheckpoint, ChangingSnapshotGateway
    from ai_service.models.single_quality_review import SingleReviewResult
    from test_subgraphs import request_for
    from test_vue_subgraph import make_workflow
    class FeedbackModel(FakeModel):
        async def repair(self, artifact, context):
            self.calls.append(("repair", deepcopy(context)))
            work = context["repairWork"]
            if work["completedIssueIndexes"]:
                return ModelTurn("交回验证")
            if not work["activePlan"]:
                return ModelTurn("活动两文件", repair_plan={"issueIndex": 0, "files": ["src/App.vue", "src/Card.vue"]},
                                 tool_calls=[ToolCall("file_read", {"relativeFilePath": "src/App.vue"})])
            if work["activeWriteSucceeded"]:
                return ModelTurn("待验证", completed_issue_index=0)
            feedback = context.get("repairPlanFeedback", "")
            if "省略repairPlan" in feedback and "activePlan.files" in feedback:
                return ModelTurn("沿原活动集最小写入", tool_calls=[ToolCall("file_write", {
                    "relativeFilePath": "src/App.vue", "content": "匿名有效修改",
                })])
            return ModelTurn("误重报为单文件计划", repair_plan={"issueIndex": 0, "files": ["src/App.vue"]},
                             tool_calls=[ToolCall("file_read", {"relativeFilePath": "src/App.vue"})])
    model = FeedbackModel(reviews=[SingleReviewResult(verdict="REPAIR", issues=ISSUES[:1]), True])
    checkpoint, gateway = MemoryCheckpoint(), ChangingSnapshotGateway()
    # 两文件计划须确实出现在验证快照安全清单中，避免先命中无关的越界门禁。
    gateway.vue_source_snapshot["files"].append({"path": "src/Card.vue", "content": "<template>卡片</template>", "truncated": False})
    gateway.vue_source_snapshot.update(eligibleFileCount=2, includedFileCount=2)
    events = await make_workflow(settings, model, checkpoint, gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == "completed"
    assert sum(e.type in {"completed", "failed"} for e in events) == 1
    assert sum(c["name"] == "project_build" for c in gateway.calls) == 2
    assert len([c for c in model.calls if c[0] == "repair"]) == 5
    feedbacks = [ctx.get("repairPlanFeedback", "") for name, ctx in model.calls if name == "repair"]
    assert any("省略repairPlan" in feedback for feedback in feedbacks)
    assert all("WORKSET_SWITCH_BEFORE_WRITE" not in feedback and "src/" not in feedback for feedback in feedbacks)


async def test_other_rejection_keeps_original_feedback(settings):
    _, nodes, state, _ = make_nodes(settings, work=initial_repair_work(ISSUES, FILES), response=turn())
    result = await nodes["validate_turn"](state)
    assert "第一个未完成问题返回repairPlan" in result["context"]["repairPlanFeedback"]
    assert "省略repairPlan" not in result["context"]["repairPlanFeedback"]
