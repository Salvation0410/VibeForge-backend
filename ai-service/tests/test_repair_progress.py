"""用真实嵌套图与离线模型验证拒绝依据、无进展、预算和唯一终态。"""

import json
from copy import deepcopy

import pytest

from ai_service.models.base import ModelTurn
from ai_service.models.single_quality_review import parse_single_review
from ai_service.orchestration.repair_progress import vue_snapshot_fingerprint
from ai_service.orchestration.events import EventEmitter
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint
from test_subgraphs import CleaningGraphCheckpoint, request_for
from test_vue_subgraph import make_workflow, initial
from test_quality_review_diagnostics import REPAIR, PASS, ISSUE


class SequenceModel(FakeModel):
    def __init__(self, candidates, reviews=None):
        super().__init__(reviews=reviews or [False] * 8)
        self.candidates = iter(candidates)

    async def generate(self, branch, context):
        self.calls.append(("generate", {"context": deepcopy(context)}))
        return ModelTurn(next(self.candidates), finish_reason="STOP")

    async def repair(self, artifact, context):
        self.calls.append(("repair", {"context": deepcopy(context)}))
        return ModelTurn(next(self.candidates), finish_reason="STOP")


def snapshot(text):
    return {"files": [{"path": "src/App.vue", "content": text, "truncated": False}],
            "eligibleFileCount": 1, "includedFileCount": 1, "omittedFileCount": 0, "truncated": False}


class SnapshotGateway(FakeToolGateway):
    def __init__(self, snapshots):
        super().__init__()
        self.snapshots = iter(snapshots)

    async def invoke(self, name, arguments, **kwargs):
        result = await super().invoke(name, arguments, **kwargs)
        return next(self.snapshots) if name == "vue_source_snapshot" else result


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
@pytest.mark.parametrize("candidates,review_count", [(["A", "A"], 1), (["A", "B", "A"], 2)])
async def test_repeated_candidate_and_cycle_stop_before_another_review(settings, branch, candidates, review_count):
    model = SequenceModel(candidates)
    gateway = SnapshotGateway([snapshot(text) for text in candidates])
    workflow = make_workflow(settings, model, MemoryCheckpoint(), gateway)
    events = await workflow.run(request_for(branch))
    assert events[-1].error.code == "REPAIR_NO_PROGRESS"
    assert sum(event.type in {"failed", "completed"} for event in events) == 1
    assert sum(name == "review" for name, _ in model.calls) == review_count
    assert sum(name == "repair" for name, _ in model.calls) == review_count
    assert not any(call["name"] == "artifact_publish" for call in gateway.calls)
    if branch != "VUE_PROJECT":
        assert sum(call["name"] == "artifact_validate" for call in gateway.calls) == review_count


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
async def test_changed_candidate_reviews_again_and_receives_structured_feedback(settings, branch):
    model = SequenceModel(["A", "A "], [parse_single_review(REPAIR, "STOP"), parse_single_review(PASS, "STOP")])
    gateway = SnapshotGateway([snapshot("A"), snapshot("A ")])
    checkpoint = CleaningGraphCheckpoint()
    workflow = make_workflow(settings, model, checkpoint, gateway)
    graph = workflow._build_graph(EventEmitter("subgraph-test"), "42:subgraph-test", {})
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    result = await graph.ainvoke({**initial(), "code_gen_type": branch}, config)
    assert result["quality_passed"] is True
    repair_context = next(payload["context"] for name, payload in model.calls if name == "repair")
    assert json.loads(repair_context["qualityReview"]["repairFeedback"])["issues"][0] == ISSUE
    assert "candidate_fingerprints" not in result
    for name, payload in model.calls:
        assert "candidate_fingerprints" not in str(payload)
    entries = [entry async for entry in checkpoint.saver.alist(config)]
    assert any("candidate_fingerprints" in entry.checkpoint["channel_values"] for entry in entries)
    for entry in entries:
        namespace = entry.config["configurable"]["checkpoint_ns"]
        if "|repair:" in namespace or not namespace:
            assert "candidate_fingerprints" not in entry.checkpoint["channel_values"]
    assert "private-evidence" not in str(workflow._checkpoint_payload(result, "quality_review"))


@pytest.mark.parametrize("change", [
    {"truncated": True}, {"omittedFileCount": 1}, {"includedFileCount": 0},
    {"eligibleFileCount": True}, {"files": []},
    {"files": [{"path": "src/App.vue", "content": "A", "truncated": True}]},
])
def test_incomplete_snapshot_never_claims_equivalence(change):
    assert vue_snapshot_fingerprint({**snapshot("A"), **change}) is None


def test_snapshot_order_is_irrelevant_but_path_and_content_matter():
    value = snapshot("A")
    value["files"].append({"path": "src/B.vue", "content": "B", "truncated": False})
    value.update(eligibleFileCount=2, includedFileCount=2)
    reordered = {**value, "files": list(reversed(value["files"]))}
    assert vue_snapshot_fingerprint(value) == vue_snapshot_fingerprint(reordered)
    changed = deepcopy(value)
    changed["files"][0]["path"] = "src/C.vue"
    assert vue_snapshot_fingerprint(value) != vue_snapshot_fingerprint(changed)


async def test_truncated_vue_snapshot_keeps_existing_review_budget(settings):
    value = {**snapshot("A"), "truncated": True}
    model = SequenceModel(["A", "A", "A"], [False, False, True])
    events = await make_workflow(settings, model, MemoryCheckpoint(), SnapshotGateway([value] * 3)).run(request_for("VUE_PROJECT"))
    assert events[-1].type == "completed"
    assert sum(name == "review" for name, _ in model.calls) == 3


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
async def test_changed_candidates_still_obey_repair_limit(settings, branch):
    model = SequenceModel(["A", "B", "C"])
    events = await make_workflow(settings, model, MemoryCheckpoint(), SnapshotGateway([snapshot(x) for x in "ABC"])).run(request_for(branch))
    assert events[-1].type == "failed"
    assert sum(name == "repair" for name, _ in model.calls) == 2
    assert sum(name == "review" for name, _ in model.calls) == 3


@pytest.mark.parametrize("branch", ["HTML", "VUE_PROJECT"])
async def test_cancel_after_repair_wins_over_no_progress(settings, branch):
    model = SequenceModel(["A", "A"])
    workflow = make_workflow(settings, model, MemoryCheckpoint(), SnapshotGateway([snapshot("A")] * 2))
    original = model.repair

    async def cancel(artifact, context):
        result = await original(artifact, context)
        workflow.cancellations.cancel("42:subgraph-test")
        return result

    model.repair = cancel
    events = await workflow.run(request_for(branch))
    assert events[-1].error.code == "cancelled"
    assert sum(event.type in {"failed", "completed"} for event in events) == 1
    assert sum(name == "review" for name, _ in model.calls) == 1


async def test_invalid_static_candidate_is_not_validated_repeatedly(settings):
    class InvalidGateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            return {"valid": False, "errors": ["invalid"]} if name == "artifact_validate" else result

    gateway = InvalidGateway()
    model = SequenceModel(["A", "A"])
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for("HTML"))
    assert events[-1].error.code == "REPAIR_NO_PROGRESS"
    assert not any(name == "review" for name, _ in model.calls)


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
async def test_rejection_details_do_not_leak_into_events_or_status(settings, branch):
    model = SequenceModel(["A", "A"], [parse_single_review(REPAIR, "STOP")])
    checkpoint = MemoryCheckpoint()
    workflow = make_workflow(settings, model, checkpoint, SnapshotGateway([snapshot("A")] * 2))
    events = await workflow.run(request_for(branch))
    public = json.dumps([event.model_dump() for event in events])
    assert "private-" not in public
    assert "private-" not in str(checkpoint.saved)
    assert events[-1].error.code == "REPAIR_NO_PROGRESS"


async def test_new_validation_failure_does_not_reuse_previous_model_feedback(settings):
    class ValidationSequence(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            if name == "artifact_validate" and arguments["artifact"] == "B":
                return {"valid": False, "errors": ["new validation error"]}
            return result

    model = SequenceModel(["A", "B", "C"], [parse_single_review(REPAIR, "STOP"), True])
    events = await make_workflow(settings, model, MemoryCheckpoint(), ValidationSequence()).run(request_for("HTML"))
    contexts = [payload["context"] for name, payload in model.calls if name == "repair"]
    assert "qualityReview" in contexts[0]
    assert "qualityReview" not in contexts[1]
    assert contexts[1]["validation"]["errors"] == ["new validation error"]
    assert events[-1].type == "completed"


@pytest.mark.parametrize("branch", ["HTML", "VUE_PROJECT"])
async def test_invalid_structured_output_stops_with_stable_error_without_repair(settings, branch):
    class InvalidReview(FakeModel):
        async def review(self, artifact, context):
            return parse_single_review('{"verdict":"REPAIR","issues":[],"private":"source"}', "STOP")

    model = InvalidReview()
    workflow = make_workflow(settings, model, MemoryCheckpoint())
    events = await workflow.run(request_for(branch))
    assert events[-1].error.code == "QUALITY_REVIEW_INVALID_OUTPUT"
    assert events[-1].error.message == "QUALITY_REVIEW_INVALID_OUTPUT"
    assert sum(event.type in {"failed", "completed"} for event in events) == 1
    assert not any(name == "repair" for name, _ in model.calls)


async def test_vue_multi_reviewer_also_stops_unchanged_snapshot(settings):
    from ai_service.models.quality_review import ReviewerRole
    from test_api import reviewer_result, IssueSeverity

    settings.multi_agent_review_enabled = True
    model = FakeModel(role_reviews={ReviewerRole.REQUIREMENT: [
        reviewer_result(ReviewerRole.REQUIREMENT, severity=IssueSeverity.MAJOR),
    ]})
    workflow = make_workflow(settings, model, MemoryCheckpoint())
    events = await workflow.run(request_for("VUE_PROJECT"))
    assert events[-1].error.code == "REPAIR_NO_PROGRESS"
    assert sum(name == "review_role" for name, _ in model.calls) == 3


async def test_saved_fingerprint_survives_graph_rebuild_without_repeating_review(settings, monkeypatch):
    from functools import partial
    import ai_service.orchestration.workflow as workflow_module
    from ai_service.orchestration.subgraphs.verification_repair import build_verification_repair_subgraph

    checkpoint = CleaningGraphCheckpoint()
    model = SequenceModel(["A", "A"])
    workflow = make_workflow(settings, model, checkpoint)
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    with monkeypatch.context() as context:
        context.setattr(workflow_module, "build_verification_repair_subgraph", partial(
            build_verification_repair_subgraph, interrupt_after=["quality_review"]))
        graph = workflow._build_graph(EventEmitter("subgraph-test"), "42:subgraph-test", {})
        await graph.ainvoke({**initial(), "code_gen_type": "HTML"}, config)
    rebuilt = workflow._build_graph(EventEmitter("subgraph-test"), "42:subgraph-test", {})
    with pytest.raises(ValueError, match="REPAIR_NO_PROGRESS"):
        await rebuilt.ainvoke(None, config)
    assert sum(name == "review" for name, _ in model.calls) == 1


@pytest.mark.parametrize("stage", ["review", "snapshot"])
async def test_cooperative_cancel_from_review_or_snapshot_is_preserved(settings, stage):
    from ai_service.orchestration.cancellation import GenerationCancelled

    class CancelModel(FakeModel):
        async def review(self, artifact, context):
            raise GenerationCancelled()

    class CancelGateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            if name == "vue_source_snapshot" and stage == "snapshot":
                raise GenerationCancelled()
            return await super().invoke(name, arguments, **kwargs)

    model = CancelModel()
    events = await make_workflow(settings, model, MemoryCheckpoint(), CancelGateway()).run(request_for("VUE_PROJECT"))
    assert events[-1].error.code == "cancelled"
    assert sum(event.type in {"failed", "completed"} for event in events) == 1
    assert not any(name == "repair" for name, _ in model.calls)
