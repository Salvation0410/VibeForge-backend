"""解析异常沿原失败路径终止，仅将固定诊断保存到验证历史。"""

import asyncio
from copy import deepcopy
import logging
import os
from uuid import uuid4

import pytest

from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint, STATUS_UPSERT_SQL
from ai_service.models.single_quality_review import OUTPUT_FAILURE_REASONS, SingleReviewOutputError
from ai_service.models.input_review import InputReviewError
from ai_service.models.verification_diagnostics import safe_verification_history
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.nodes.verification import create_verification_nodes
from ai_service.orchestration.workflow import GenerationWorkflow
from conftest import FakeModel, MemoryCheckpoint
from test_subgraphs import CleaningGraphCheckpoint, request_for
from test_verification_failure_diagnostics import BuildRepairsGateway
from test_vue_subgraph import make_workflow


def output_error(incomplete=False):
    return SingleReviewOutputError(incomplete=incomplete, reason="INVALID_SCHEMA")


class FailedReviewModel(FakeModel):
    def __init__(self, error=None, value=None):
        super().__init__()
        self.error, self.value = error, value

    async def review(self, artifact, context):
        self.calls.append(("review", {"artifact": artifact, "context": context}))
        if self.error is not None:
            raise self.error
        return self.value


def error_record(**changes):
    return {"round": 2, "stage": "quality_review", "verdict": "ERROR",
            "errorCode": "QUALITY_REVIEW_INVALID_OUTPUT", "outputReason": "INVALID_SCHEMA",
            "toolCount": 7, "phaseReadCount": 0, "phaseWriteCount": 0,
            "repeatedReadCount": 0, "issueCount": 0, "issueCodes": [], **changes}


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
@pytest.mark.parametrize("incomplete", [False, True])
async def test_parse_failure_is_recorded_once_without_repair_publication_or_public_diagnostics(settings, branch, incomplete):
    checkpoint = CleaningGraphCheckpoint()
    model = FailedReviewModel(output_error(incomplete))
    workflow = make_workflow(settings, model, checkpoint)
    events = await workflow.run(request_for(branch))
    expected = "QUALITY_REVIEW_INCOMPLETE" if incomplete else "QUALITY_REVIEW_INVALID_OUTPUT"
    assert events[-1].error.code == events[-1].error.message == expected
    assert sum(event.type in {"failed", "completed"} for event in events) == 1
    assert sum(name == "review" for name, _ in model.calls) == 1
    assert not any(name == "repair" for name, _ in model.calls)
    assert not any(call["name"] == "artifact_publish" for call in workflow.tool_gateway.calls)
    summary = checkpoint.saved["42:subgraph-test"][-1]
    assert summary["node"] == "quality_review" and summary["qualityPassed"] is False
    history = summary["verification_history"]
    assert history[-1] == error_record(round=0, toolCount=0, errorCode=expected,
                                      outputReason="INCOMPLETE" if incomplete else "INVALID_SCHEMA")
    assert len(history) == (3 if branch == "VUE_PROJECT" else 2)
    assert "review_diagnostics" not in summary
    assert "outputReason" not in str([event.model_dump() for event in events])
    assert "verification_history" not in str(model.calls)
    # 请求终态仍清理全部图检查点，私有状态摘要按原规则保留。
    assert any(ns.startswith("verification_repair:") for ns in checkpoint.namespaces_before_cleanup)
    assert [entry async for entry in checkpoint.saver.alist({"configurable": {"thread_id": "42:subgraph-test"}})] == []
    assert not any(event.type == "node_status" and event.node == "quality_review"
                   and event.data.get("status") == "completed" for event in events)


async def test_last_round_error_preserves_prior_build_history_and_budget(settings):
    model, checkpoint = FailedReviewModel(output_error()), MemoryCheckpoint()
    events = await make_workflow(settings, model, checkpoint, BuildRepairsGateway()).run(request_for("VUE_PROJECT"))
    history = checkpoint.saved["42:subgraph-test"][-1]["verification_history"]
    assert events[-1].error.code == "QUALITY_REVIEW_INVALID_OUTPUT"
    assert len(history) == 7 and history[-1]["round"] == 2
    assert history[-1]["verdict"] == "ERROR"
    assert sum(name == "repair" for name, _ in model.calls) == 2
    assert sum(name == "review" for name, _ in model.calls) == 1


def direct_state():
    return {"app_id": "42", "request_id": "req", "code_gen_type": "HTML", "artifact": "private-source",
            "prompt": "private-prompt", "context": {}, "validation": {"valid": True},
            "repair_count": 2, "tool_call_count": 7}


def direct_node(settings, model, checkpoint, cancellations=None):
    workflow = make_workflow(settings, model, checkpoint, cancellations=cancellations)
    return workflow, create_verification_nodes(workflow, EventEmitter("req"), "42:req", {})["quality_review"]


async def test_bad_injected_return_has_unclassified_diagnostic_and_stable_public_error(settings):
    checkpoint = MemoryCheckpoint()
    _, node = direct_node(settings, FailedReviewModel(value={"private": "source"}), checkpoint)
    with pytest.raises(SingleReviewOutputError):
        await node(direct_state())
    assert checkpoint.saved["42:req"][-1]["verification_history"] == [error_record(outputReason="UNCLASSIFIED")]
    assert "private" not in str(checkpoint.saved)


@pytest.mark.parametrize("failure", [RuntimeError("private-checkpoint"), ValueError("private-checkpoint")])
async def test_checkpoint_failure_does_not_replace_original_parse_error(settings, caplog, failure):
    class BrokenCheckpoint(MemoryCheckpoint):
        async def save(self, thread_id, state):
            raise failure

    original = output_error()
    _, node = direct_node(settings, FailedReviewModel(original), BrokenCheckpoint())
    with caplog.at_level(logging.INFO), pytest.raises(SingleReviewOutputError) as caught:
        await node(direct_state())
    assert caught.value is original
    assert "CHECKPOINT_WRITE_FAILED" in caplog.text and "private" not in caplog.text


@pytest.mark.parametrize("failure", [asyncio.CancelledError(), GenerationCancelled()])
async def test_cancellation_during_failure_record_save_is_not_swallowed(settings, failure):
    class CancelledCheckpoint(MemoryCheckpoint):
        async def save(self, thread_id, state):
            raise failure

    _, node = direct_node(settings, FailedReviewModel(output_error()), CancelledCheckpoint())
    with pytest.raises(type(failure)) as caught:
        await node(direct_state())
    assert caught.value is failure


@pytest.mark.parametrize("save_raises", [False, True])
async def test_cooperative_cancel_during_save_takes_precedence(settings, save_raises):
    from ai_service.orchestration.cancellation import CancellationRegistry

    cancellations = CancellationRegistry()

    class CancellingCheckpoint(MemoryCheckpoint):
        async def save(self, thread_id, state):
            cancellations.cancel(thread_id)
            if save_raises:
                raise RuntimeError("private-checkpoint")
            await super().save(thread_id, state)

    _, node = direct_node(settings, FailedReviewModel(output_error()), CancellingCheckpoint(), cancellations)
    with pytest.raises(GenerationCancelled):
        await node(direct_state())


@pytest.mark.parametrize("failure", [RuntimeError("private-provider"), asyncio.CancelledError(), GenerationCancelled(),
                                     InputReviewError("INPUT_CONTEXT_BUDGET_EXCEEDED", "private-budget")])
async def test_other_review_errors_do_not_create_parse_failure_history(settings, failure):
    checkpoint = MemoryCheckpoint()
    _, node = direct_node(settings, FailedReviewModel(failure), checkpoint)
    with pytest.raises(type(failure)) as caught:
        await node(direct_state())
    assert caught.value is failure and not checkpoint.saved


async def test_diagnostic_logging_failure_preserves_original_error_and_history(settings, monkeypatch):
    from ai_service.orchestration.nodes import verification

    def broken_log(*args, **kwargs):
        raise RuntimeError("private-log")

    monkeypatch.setattr(verification.logger, "info", broken_log)
    original, checkpoint = output_error(), MemoryCheckpoint()
    _, node = direct_node(settings, FailedReviewModel(original), checkpoint)
    with pytest.raises(SingleReviewOutputError) as caught:
        await node(direct_state())
    assert caught.value is original
    assert checkpoint.saved["42:req"][-1]["verification_history"] == [error_record()]


async def test_same_round_replay_replaces_record_and_does_not_mutate_input(settings):
    checkpoint = MemoryCheckpoint()
    _, node = direct_node(settings, FailedReviewModel(output_error()), checkpoint)
    previous = error_record(outputReason="INVALID_JSON")
    state = {**direct_state(), "verification_history": [previous]}
    before = deepcopy(state)
    with pytest.raises(SingleReviewOutputError):
        await node(state)
    assert state == before
    assert checkpoint.saved["42:req"][-1]["verification_history"] == [error_record()]


@pytest.mark.parametrize("change", [
    {"outputReason": "private-source"}, {"outputReason": []}, {"verdict": "REPAIR"},
    {"stage": "project_build"}, {"errorCode": "QUALITY_REVIEW_FAILED"},
    {"outputReason": "INCOMPLETE"}, {"errorCode": "QUALITY_REVIEW_INCOMPLETE"},
    {"issueCount": 1, "issueCodes": ["FUNCTIONAL_ERROR"]}, {"path": "private-source"},
    {"toolCount": True}, {"outputReason": "NONE"},
])
def test_both_history_projections_reject_untrusted_or_inconsistent_failure_records(change):
    good = [error_record()]
    base = {"app_id": "42", "request_id": "req", "code_gen_type": "HTML"}
    assert safe_verification_history(good) == good
    payload = GenerationWorkflow._checkpoint_payload({**base, "verification_history": good}, "quality_review")
    assert PostgresCheckpoint._normalize_state(payload)["verification_history"] == good
    bad = [{**error_record(), **change}]
    assert "verification_history" not in GenerationWorkflow._checkpoint_payload({**base, "verification_history": bad}, "quality_review")
    assert "verification_history" not in PostgresCheckpoint._normalize_state({**payload, "verification_history": bad})


def test_error_reason_is_required_but_old_pass_repair_shape_is_compatible():
    bad = error_record()
    del bad["outputReason"]
    assert safe_verification_history([bad]) is None
    old = {key: value for key, value in error_record(verdict="REPAIR", errorCode="QUALITY_REVIEW_FAILED").items()
           if key != "outputReason"}
    assert safe_verification_history([old]) == [old]
    assert safe_verification_history([{**old, "errorCode": "QUALITY_REVIEW_INVALID_OUTPUT"}]) is None


@pytest.mark.parametrize("reason", sorted(OUTPUT_FAILURE_REASONS))
def test_all_fixed_reasons_survive_both_projections(reason):
    history = [error_record(outputReason=reason, errorCode=(
        "QUALITY_REVIEW_INCOMPLETE" if reason == "INCOMPLETE" else "QUALITY_REVIEW_INVALID_OUTPUT"))]
    base = {"app_id": "42", "request_id": "req", "code_gen_type": "HTML", "verification_history": history}
    payload = GenerationWorkflow._checkpoint_payload(base, "quality_review")
    assert PostgresCheckpoint._normalize_state(payload)["verification_history"] == history


@pytest.mark.skipif(os.getenv("AI_SERVICE_POSTGRES_INTEGRATION") != "true", reason="需显式开启真实PostgreSQL")
async def test_real_postgres_keeps_error_reason_through_following_summary():
    from psycopg import AsyncConnection
    from psycopg.types.json import Jsonb
    from ai_service.config import Settings

    request_id = uuid4().hex
    thread_id = "review-failure-integration:" + request_id
    connection = await AsyncConnection.connect(Settings().checkpoint_postgres_url)
    try:
        history = [error_record()]
        base = {"app_id": "42", "request_id": request_id, "code_gen_type": "HTML", "repair_count": 2, "tool_call_count": 7}
        for node, state in [("quality_review", {**base, "verification_history": history}), ("workflow", base)]:
            payload = PostgresCheckpoint._normalize_state(GenerationWorkflow._checkpoint_payload(state, node))
            await connection.execute(STATUS_UPSERT_SQL, (
                thread_id, 42, request_id, "HTML", node, False, 2, 7, Jsonb(payload), 60,
            ))
            cursor = await connection.execute("SELECT status_payload FROM ai_workflow_status WHERE thread_id=%s", (thread_id,))
            assert (await cursor.fetchone())[0]["verification_history"] == history
    finally:
        # 仅回滚本测试唯一行，不清理既有业务请求或服务检查点。
        await connection.rollback()
        await connection.close()
