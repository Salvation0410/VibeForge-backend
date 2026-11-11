"""共享契约和真实编排上的显式编辑门控，供应商与项目网关均使用 Fake。"""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from ai_service.api.schemas import GenerationRequest
from ai_service.models.structured_edit import StructuredEditIntent
from ai_service.models.input_review import InputReviewError, InputReviewResult
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.nodes.preparation import create_preparation_node
from ai_service.orchestration.cancellation import GenerationCancelled
from conftest import FakeToolGateway, parse_ndjson
from test_generation_preparation import RewriteModel

FIXTURES = json.loads((Path(__file__).parent / "fixtures/structured-edit-v1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", FIXTURES["valid"], ids=lambda case: case["intent"]["property"])
def test_shared_canonical_contract(case):
    intent = StructuredEditIntent.model_validate(case["intent"])
    assert intent.canonical_prompt() == case["message"]
    req = GenerationRequest.model_validate({"requestId": "r", "appId": "a", "prompt": case["message"],
                                            "codeGenType": "HTML", "editIntent": case["intent"]})
    assert req.edit_intent == intent


@pytest.mark.parametrize("case", FIXTURES["invalid"], ids=lambda case: case["name"])
def test_shared_invalid_contract(case):
    with pytest.raises(ValueError):
        StructuredEditIntent.model_validate(case["intent"])


@pytest.mark.parametrize("suffix", ["\n额外增加付款", " ", "\r\n"])
def test_raw_prompt_must_exactly_match_explicit_fields(suffix):
    case = FIXTURES["valid"][0]
    with pytest.raises(ValueError, match="STRUCTURED_EDIT_PROMPT_MISMATCH"):
        GenerationRequest.model_validate({"requestId": "r", "appId": "a", "prompt": case["message"] + suffix,
                                          "codeGenType": "HTML", "editIntent": case["intent"]})


def post_edit(app, *, branch="HTML", case=None, metadata=None):
    case = case or FIXTURES["valid"][0]
    with TestClient(app) as client:
        return client.post("/internal/v1/generations:stream", headers={"Authorization": "Bearer test-secret"},
                           json={"requestId": "r", "appId": "a", "codeGenType": branch,
                                 "prompt": case["message"], "editIntent": case["intent"], "metadata": metadata or {}})


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
def test_full_workflow_preserves_edit_through_repair_without_rewrite(app_factory, settings, branch):
    model = RewriteModel(reviews=[False, True])
    gateway = FakeToolGateway(artifact_context={"exists": True, "entries": ["src/App.vue"]})
    app = app_factory(model=model, gateway=gateway)
    app.state.workflow.settings = settings.model_copy(update={"prompt_rewrite_enabled": True,
                                                            "frontend_skills_enabled": True,
                                                            "preparation_diagnostics_enabled": True})
    events = parse_ndjson(post_edit(app, branch=branch))
    assert events[-1]["type"] == "completed", events
    assert model.rewrite_calls == []
    contexts = [payload["context"] for name, payload in model.calls if name in {"generate", "repair", "review"}]
    assert len(contexts) == 4
    for context in contexts:
        assert context["prompt"] == FIXTURES["valid"][0]["message"]
        assert context["editIntent"] == FIXTURES["valid"][0]["intent"]
        assert context["generationPreparation"]["rewriteStatus"] == "skipped"
        assert context["generationPreparation"]["brief"] is None
        assert "preserve-style" in {skill["id"] for skill in context["generationPreparation"]["skills"]}
    assert "image_search" not in [call["name"] for call in gateway.calls]
    assert "STRUCTURED_EDIT" not in json.dumps(events)  # 诊断原因不扩展公开进度协议。


@pytest.mark.parametrize("rewrite,feature,expected,count", [(True, False, "ready", 1),
                                                          (False, True, "disabled", 0),
                                                          (True, True, "skipped", 0)])
def test_rewrite_and_feature_flags_are_independent(app_factory, settings, rewrite, feature, expected, count):
    model = RewriteModel()
    app = app_factory(model=model, gateway=FakeToolGateway(artifact_context={"exists": True}))
    app.state.workflow.settings = settings.model_copy(update={"prompt_rewrite_enabled": rewrite,
                                                            "frontend_skills_enabled": True,
                                                            "structured_edit_enabled": feature})
    events = parse_ndjson(post_edit(app))
    assert events[-1]["type"] == "completed"
    assert len(model.rewrite_calls) == count
    context = next(payload["context"] for name, payload in model.calls if name == "generate")
    assert context["generationPreparation"]["rewriteStatus"] == expected


def test_missing_artifact_rejected_before_images_or_generation(app_factory, settings):
    model, gateway = RewriteModel(), FakeToolGateway()
    app = app_factory(model=model, gateway=gateway)
    events = parse_ndjson(post_edit(app, branch="VUE_PROJECT"))
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "STRUCTURED_EDIT_REQUIRES_ARTIFACT"
    assert [call["name"] for call in gateway.calls] == ["artifact_context"]
    assert not model.calls and not model.rewrite_calls


@pytest.mark.parametrize("entries", [[], ["package.json"], None])
def test_empty_vue_directory_is_not_an_editable_artifact(app_factory, entries):
    model = RewriteModel()
    gateway = FakeToolGateway(artifact_context={"exists": True, "entries": entries})
    events = parse_ndjson(post_edit(app_factory(model=model, gateway=gateway), branch="VUE_PROJECT"))
    assert events[-1]["error"]["code"] == "STRUCTURED_EDIT_REQUIRES_ARTIFACT"
    assert [call["name"] for call in gateway.calls] == ["artifact_context"]
    assert not model.calls and not model.rewrite_calls


def test_input_review_still_blocks_structured_edit(app_factory, settings):
    class RejectModel(RewriteModel):
        async def review_input(self, context):
            return InputReviewResult(decision="REJECT", reason="SAFETY", message="输入拒绝", questions=[])
    model, gateway = RejectModel(), FakeToolGateway(artifact_context={"exists": True})
    app = app_factory(model=model, gateway=gateway)
    app.state.workflow.input_reviewer.settings = settings.model_copy(update={"input_review_enabled": True})
    events = parse_ndjson(post_edit(app))
    assert events[-1]["error"]["code"] == "INPUT_REJECTED"
    assert not gateway.calls and not model.rewrite_calls


def test_validation_errors_do_not_echo_request_body(app_factory):
    app = app_factory()
    case = deepcopy(FIXTURES["valid"][0])
    case["message"] += "PRIVATE_DEMAND"
    response = post_edit(app, case=case, metadata={"secret": "PRIVATE_METADATA"})
    assert response.status_code == 422
    assert "PRIVATE" not in response.text


def test_structured_edit_never_bypasses_hard_input_budget(app_factory, settings):
    model, gateway = RewriteModel(), FakeToolGateway(artifact_context={"exists": True})
    app = app_factory(model=model, gateway=gateway)
    app.state.workflow.input_reviewer.settings = settings.model_copy(update={"model_context_window_tokens": 1024})
    events = parse_ndjson(post_edit(app))
    assert events[-1]["error"]["code"] == "INPUT_CONTEXT_BUDGET_EXCEEDED"
    assert not gateway.calls and not model.rewrite_calls


@pytest.mark.asyncio
async def test_metadata_cannot_claim_structured_skip_and_cached_reason_survives(settings):
    model = RewriteModel()
    config = settings.model_copy(update={"prompt_rewrite_enabled": True, "frontend_skills_enabled": True,
                                         "preparation_diagnostics_enabled": True})
    workflow = SimpleNamespace(settings=config, model=model, _raise_if_cancelled=lambda _: None)
    node = create_preparation_node(workflow, EventEmitter("r"), "a:r")
    case = FIXTURES["valid"][0]
    state = {"prompt": case["message"], "code_gen_type": "HTML", "context": {
        "prompt": case["message"], "currentArtifact": {"exists": True}, "metadata": {"editIntent": case["intent"]}}}
    result = await node(state)
    assert result["preparation_diagnostics"]["rewriteStatus"] == "ready" and len(model.rewrite_calls) == 1
    state["edit_intent"] = case["intent"]
    result = await node(state)
    assert result["preparation_diagnostics"]["reason"] == "STRUCTURED_EDIT"
    state.update(context=result["context"])
    result = await node(state)
    assert result["preparation_diagnostics"]["reason"] == "STRUCTURED_EDIT" and len(model.rewrite_calls) == 1
    workflow._raise_if_cancelled = lambda _: (_ for _ in ()).throw(GenerationCancelled())
    state["context"].pop("generationPreparation")
    with pytest.raises(GenerationCancelled):
        await node(state)


@pytest.mark.asyncio
async def test_node_revalidates_mismatched_checkpoint_payload(settings):
    case = FIXTURES["valid"][0]
    workflow = SimpleNamespace(settings=settings, model=RewriteModel(), _raise_if_cancelled=lambda _: None)
    node = create_preparation_node(workflow, EventEmitter("r"), "a:r")
    with pytest.raises(InputReviewError, match="STRUCTURED_EDIT_PROMPT_MISMATCH"):
        await node({"edit_intent": case["intent"], "code_gen_type": "HTML", "context": {
            "prompt": "新增功能", "currentArtifact": {"exists": True}, "generationPreparation": {"rewriteStatus": "skipped"}}})
