"""失败阶段、有限修复和诊断持久化使用真实嵌套图验证，不调用供应商。"""

import pytest

from ai_service.infrastructure.langsmith_tracing import _safe_metadata
from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint
from ai_service.models.single_quality_review import parse_single_review
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.nodes.verification import create_verification_nodes
from ai_service.orchestration.workflow import GenerationWorkflow
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint
from test_quality_review_diagnostics import REPAIR
from test_subgraphs import request_for
from test_vue_subgraph import make_workflow


class BuildRepairsGateway(FakeToolGateway):
    async def invoke(self, name, arguments, **kwargs):
        result = await super().invoke(name, arguments, **kwargs)
        if name == "project_build":
            count = sum(call["name"] == name for call in self.calls)
            if count < 3:
                return {"built": False, "errorCode": "VUE_COMPONENT_CONTRACT_FAILED", "message": "missing prop"}
        return result


async def test_build_repairs_exhausted_then_review_rejects_with_safe_persisted_diagnostic(settings):
    model = FakeModel(reviews=[parse_single_review(REPAIR, "STOP")])
    checkpoint = MemoryCheckpoint()
    gateway = BuildRepairsGateway()
    workflow = make_workflow(settings, model, checkpoint, gateway)
    records = []

    class RecordingTracer:
        async def record(self, **kwargs):
            records.append(kwargs)

    workflow.tracer = RecordingTracer()
    events = await workflow.run(request_for("VUE_PROJECT"))
    error = events[-1].error
    assert error.code == "QUALITY_REVIEW_FAILED"
    assert "构建已通过" in error.message and "2/2" in error.message
    assert "[]" not in error.message and "private-" not in error.message
    assert sum(event.type in {"failed", "completed"} for event in events) == 1
    assert sum(name == "repair" for name, _ in model.calls) == 2
    assert sum(name == "review" for name, _ in model.calls) == 1
    assert not any(call["name"] == "artifact_publish" for call in gateway.calls)
    summary = checkpoint.saved["42:subgraph-test"][-1]
    assert summary["review_diagnostics"] == {
        "stage": "quality_review", "verdict": "REPAIR", "issueCount": 1,
        "issueCodes": ["SCOPE_VIOLATION"],
    }
    assert "private-" not in str(summary)
    assert checkpoint.cleaned_graph_threads == ["42:subgraph-test"]
    assert all("review_diagnostics" not in str(payload) for _, payload in model.calls)
    metadata = next(record["metadata"] for record in records if record["name"] == "quality_review")
    assert metadata["request_id"] == "subgraph-test"
    assert metadata["review_issue_count"] == 1 and metadata["review_issue_codes"] == "SCOPE_VIOLATION"
    assert "private-" not in str(records)


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
async def test_legacy_boolean_reject_has_explicit_error_without_fabricated_issues(settings, branch):
    settings.max_repair_attempts = 0
    checkpoint = MemoryCheckpoint()
    events = await make_workflow(settings, FakeModel(reviews=[False]), checkpoint).run(request_for(branch))
    assert events[-1].error.code == "QUALITY_REVIEW_FAILED"
    assert checkpoint.saved["42:subgraph-test"][-1]["review_diagnostics"]["issueCount"] == 0


async def test_hard_validation_failure_is_distinct_and_does_not_echo_raw_errors(settings):
    class InvalidGateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            return {"valid": False, "errors": ["private-source"]} if name == "artifact_validate" else result

    settings.max_repair_attempts = 0
    model = FakeModel()
    events = await make_workflow(settings, model, MemoryCheckpoint(), InvalidGateway()).run(request_for("HTML"))
    assert events[-1].error.code == "ARTIFACT_VALIDATION_FAILED"
    assert "private" not in events[-1].error.message
    assert not any(name == "review" for name, _ in model.calls)


async def test_new_candidate_clears_previous_review_diagnostic(settings):
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint())
    nodes = create_verification_nodes(workflow, EventEmitter("req"), "42:req", {})
    result = await nodes["artifact_validation"]({
        "app_id": "42", "request_id": "req", "code_gen_type": "VUE_PROJECT",
        "review_diagnostics": {"verdict": "REPAIR"},
    })
    assert result["review_diagnostics"] == {}


def test_checkpoint_projection_and_trace_filter_reject_untrusted_diagnostic_content():
    base = {"app_id": "42", "request_id": "r", "code_gen_type": "HTML"}
    good = {"stage": "quality_review", "verdict": "REPAIR", "issueCount": 1,
            "issueCodes": ["FUNCTIONAL_ERROR"]}
    payload = GenerationWorkflow._checkpoint_payload({**base, "review_diagnostics": good}, "quality_review")
    assert PostgresCheckpoint._normalize_state(payload)["review_diagnostics"] == good
    for change in [{"issueCodes": ["private-source"]}, {"summary": "private-source"},
                   {"issueCount": True}, {"issueCount": 13}, {"stage": "private-stage"},
                   {"verdict": "PASS"}, {"verdict": []}, {"issueCodes": "FUNCTIONAL_ERROR"}]:
        bad = {**good, **change}
        payload = GenerationWorkflow._checkpoint_payload({**base, "review_diagnostics": bad}, "quality_review")
        assert "review_diagnostics" not in payload
        trusted_payload = GenerationWorkflow._checkpoint_payload(base, "quality_review")
        trusted_payload["review_diagnostics"] = bad
        assert "review_diagnostics" not in PostgresCheckpoint._normalize_state(trusted_payload)
    assert _safe_metadata({"review_verdict": "REPAIR", "review_issue_count": 1,
                           "review_issue_codes": "FUNCTIONAL_ERROR"}) == {
        "review_verdict": "REPAIR", "review_issue_count": 1, "review_issue_codes": "FUNCTIONAL_ERROR"}
    assert _safe_metadata({"review_verdict": "private", "review_issue_count": True,
                           "review_issue_codes": "private"}) == {}


@pytest.mark.parametrize("error_code", ["VUE_ANCHOR_TARGET_MISSING", "VUE_ANCHOR_CHECK_UNAVAILABLE"])
async def test_anchor_failure_uses_original_repair_or_environment_failure_route(settings, error_code):
    class AnchorGateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            if name == "project_build" and sum(c["name"] == name for c in self.calls) == 1:
                return {"built": False, "errorCode": error_code, "message": "anchor check"}
            return result

    model = FakeModel()
    events = await make_workflow(settings, model, MemoryCheckpoint(), AnchorGateway()).run(request_for("VUE_PROJECT"))
    if error_code.endswith("UNAVAILABLE"):
        assert events[-1].error.code == error_code
        assert not any(name == "repair" for name, _ in model.calls)
    else:
        assert events[-1].type == "completed"
        assert sum(name == "repair" for name, _ in model.calls) == 1


async def test_missing_build_confirmation_is_not_misclassified_as_quality_failure(settings):
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint())
    nodes = create_verification_nodes(workflow, EventEmitter("req"), "42:req", {})
    with pytest.raises(ValueError, match="VUE_BUILD_FAILED"):
        await nodes["fail_quality"]({"code_gen_type": "VUE_PROJECT", "validation": {"valid": True}, "build": {}})


async def test_multi_reviewer_arbitrary_codes_are_classified_without_free_text(settings):
    from ai_service.models.quality_review import QualityIssue, ReviewerResult, ReviewerRole, IssueSeverity

    settings.multi_agent_review_enabled = True
    model = FakeModel(role_reviews={ReviewerRole.FUNCTION: [ReviewerResult(
        reviewer=ReviewerRole.FUNCTION, summary="private-summary",
        issues=[QualityIssue(code="private-code", category="private-category", summary="private-summary",
                             evidence="private-evidence", repair_hint="private-hint", severity=IssueSeverity.MAJOR)],
    )]})
    checkpoint = MemoryCheckpoint()
    settings.max_repair_attempts = 0
    events = await make_workflow(settings, model, checkpoint).run(request_for("VUE_PROJECT"))
    assert events[-1].error.code == "QUALITY_REVIEW_FAILED"
    diagnostic = checkpoint.saved["42:subgraph-test"][-1]["review_diagnostics"]
    assert diagnostic["issueCodes"] == ["UNCLASSIFIED"] and diagnostic["issueCount"] == 1
    assert "private-" not in str(checkpoint.saved)
