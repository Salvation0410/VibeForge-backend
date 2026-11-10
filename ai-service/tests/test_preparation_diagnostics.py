"""通过离线模型与真实 SDK 的模拟 HTTP 响应验证诊断，不发送外部请求。"""

import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
import pytest

from ai_service.config import Settings
from ai_service.infrastructure.langsmith_tracing import _safe_metadata
from ai_service.models.input_review import InputReviewError
from ai_service.models.preparation_diagnostics import PreparationFailure, safe_preparation_diagnostics
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.nodes.preparation import create_preparation_node
from ai_service.orchestration.workflow import GenerationWorkflow
from ai_service.orchestration.subgraphs.schemas import GenerationInput, VerificationInput
from test_generation_preparation import RewriteModel, brief, make_http_model, response_json


@pytest.fixture
def diagnostic_settings(settings):
    return settings.model_copy(update={"preparation_diagnostics_enabled": True,
                                       "prompt_rewrite_enabled": True, "frontend_skills_enabled": True})


async def run_prepare(settings, model, *, tracer=None, cancel=None, state=None):
    def check(_):
        if cancel is not None:
            raise cancel
    workflow = SimpleNamespace(settings=settings, model=model, _raise_if_cancelled=check, tracer=tracer)
    emitter = EventEmitter("request")
    state = state or {"context": {"prompt": "保留图片", "metadata": {"secret": "PRIVATE_METADATA"}},
                      "prompt": "保留图片", "code_gen_type": "HTML"}
    update = await create_preparation_node(workflow, emitter, "app:request")(state)
    return update, emitter.events


def logged_diagnostics(caplog):
    return [json.loads(record.message.removeprefix("preparation_diagnostics ")) for record in caplog.records
            if record.name == "ai_service.orchestration.nodes.preparation"]


def test_diagnostics_default_enabled():
    assert Settings(_env_file=None, internal_bearer_token="x", spring_gateway_base_url="http://test",
                    spring_gateway_bearer_token="x").preparation_diagnostics_enabled


@pytest.mark.asyncio
async def test_success_has_standalone_bounded_diagnostics(diagnostic_settings, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    clock = iter([100.0, 100.125])
    monkeypatch.setattr("ai_service.orchestration.nodes.preparation.monotonic", lambda: next(clock))
    model = RewriteModel()
    update, events = await run_prepare(diagnostic_settings, model)
    diagnostic = update["preparation_diagnostics"]
    assert set(diagnostic) == {"rewriteStatus", "reason", "durationMs", "modelAttempted", "briefBytes", "skillIds"}
    assert diagnostic["rewriteStatus"] == "ready" and diagnostic["reason"] == "NONE"
    assert diagnostic["durationMs"] == 125 and diagnostic["modelAttempted"] is True
    assert diagnostic["briefBytes"] == len(brief().model_dump_json().encode("utf-8"))
    assert diagnostic["skillIds"] == ["design-core@1", "accessibility@1", "static-native@1", "product-content@1"]
    assert logged_diagnostics(caplog) == [diagnostic]
    assert "preparation_diagnostics" not in str(update["context"])
    assert "durationMs" not in str(model.rewrite_calls) + str(events)
    assert "PRIVATE_METADATA" not in caplog.text and "保留图片" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("rewrite,skills", [(False, False), (False, True)])
async def test_disabled_rewrite_is_measured_without_model(diagnostic_settings, rewrite, skills):
    settings = diagnostic_settings.model_copy(update={"prompt_rewrite_enabled": rewrite, "frontend_skills_enabled": skills})
    model = RewriteModel()
    update, _ = await run_prepare(settings, model)
    diagnostic = update["preparation_diagnostics"]
    assert diagnostic["rewriteStatus"] == "disabled" and diagnostic["reason"] == "DISABLED"
    assert diagnostic["modelAttempted"] is False and diagnostic["briefBytes"] == 0
    assert diagnostic["durationMs"] >= 0 and bool(diagnostic["skillIds"]) is skills
    assert model.rewrite_calls == []


@pytest.mark.asyncio
async def test_switch_off_preserves_original_state_and_observers(diagnostic_settings, caplog):
    caplog.set_level(logging.INFO)
    settings = diagnostic_settings.model_copy(update={"preparation_diagnostics_enabled": False})
    update, _ = await run_prepare(settings, RewriteModel())
    assert set(update) == {"context"}
    assert "preparation_diagnostics" not in GenerationWorkflow._checkpoint_payload(update, "generation_prepare")
    assert logged_diagnostics(caplog) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,reason", [
    (ValueError("PRIVATE_PROVIDER_TEXT"), "PROVIDER_ERROR"),
    (PreparationFailure("INVALID_JSON"), "INVALID_JSON"),
    (PreparationFailure("INVALID_SCHEMA"), "INVALID_SCHEMA"),
    (PreparationFailure("INCOMPLETE_RESPONSE"), "INCOMPLETE_RESPONSE"),
    (TimeoutError("PRIVATE_TIMEOUT"), "TIMEOUT"),
])
async def test_fallback_reasons_are_stable(diagnostic_settings, failure, reason, caplog):
    caplog.set_level(logging.INFO)
    model = RewriteModel(error=failure)
    update, events = await run_prepare(diagnostic_settings, model)
    diagnostic = update["preparation_diagnostics"]
    assert diagnostic["rewriteStatus"] == "fallback" and diagnostic["reason"] == reason
    assert diagnostic["modelAttempted"] is True and diagnostic["briefBytes"] == 0
    assert len(model.rewrite_calls) == 1
    assert "PRIVATE_" not in caplog.text + str(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,reason", [("evidence", "INVALID_EVIDENCE"), ("bytes", "BRIEF_TOO_LARGE"),
                                       ("schema", "INVALID_SCHEMA"), ("timeout", "TIMEOUT")])
async def test_validation_and_deadline_reasons(diagnostic_settings, kind, reason):
    model = RewriteModel(result=brief("PRIVATE_FABRICATED") if kind == "evidence" else None,
                         delay=0.05 if kind == "timeout" else 0)
    if kind == "bytes":
        diagnostic_settings = diagnostic_settings.model_copy(update={"prompt_rewrite_max_bytes": 10})
    if kind == "schema":
        model.result = {"PRIVATE_FIELD": "PRIVATE_VALUE"}
    if kind == "timeout":
        diagnostic_settings = diagnostic_settings.model_copy(update={"prompt_rewrite_timeout_seconds": 0.001})
    update, _ = await run_prepare(diagnostic_settings, model)
    assert update["preparation_diagnostics"]["reason"] == reason


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,finish,reason", [
    ("PRIVATE_NOT_JSON", "stop", "INVALID_JSON"),
    ('{"PRIVATE_FIELD":"PRIVATE_VALUE"}', "stop", "INVALID_SCHEMA"),
    (brief().model_dump_json(), "length", "INCOMPLETE_RESPONSE"),
    ("x" * 9000, "stop", "BRIEF_TOO_LARGE"),
])
async def test_sdk_mock_response_classification(diagnostic_settings, raw, finish, reason, caplog):
    caplog.set_level(logging.INFO)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_json(raw, finish))
    model, http = make_http_model(diagnostic_settings, handler)
    async with http:
        update, events = await run_prepare(diagnostic_settings, model)
    assert len(requests) == 1
    assert update["preparation_diagnostics"]["reason"] == reason
    assert "PRIVATE_" not in caplog.text + str(events)


@pytest.mark.asyncio
async def test_sdk_budget_blocks_request_and_is_observable(diagnostic_settings, caplog):
    caplog.set_level(logging.INFO)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_json(brief().model_dump_json()))
    settings = diagnostic_settings.model_copy(update={"model_context_window_tokens": 100})
    model, http = make_http_model(settings, handler)
    async with http:
        with pytest.raises(InputReviewError):
            await run_prepare(settings, model)
    diagnostic = logged_diagnostics(caplog)[0]
    assert diagnostic["rewriteStatus"] == "failed" and diagnostic["reason"] == "BUDGET_EXCEEDED"
    assert diagnostic["modelAttempted"] is False and requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status,reason", [
    (GenerationCancelled(), "cancelled", "CANCELLED"),
    (asyncio.CancelledError(), "cancelled", "CANCELLED"),
    (InputReviewError("INPUT_INVALID", "PRIVATE_INPUT"), "failed", "INPUT_ERROR"),
])
async def test_terminal_failures_observable_without_fallback(diagnostic_settings, error, status, reason, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(type(error)):
        await run_prepare(diagnostic_settings, RewriteModel(error=error))
    diagnostic = logged_diagnostics(caplog)[0]
    assert diagnostic["rewriteStatus"] == status and diagnostic["reason"] == reason
    assert "PRIVATE_INPUT" not in caplog.text


@pytest.mark.asyncio
async def test_cancellation_before_call_and_failing_observer_do_not_mask_terminal(diagnostic_settings, caplog):
    class BrokenTracer:
        async def record(self, **kwargs):
            raise RuntimeError("PRIVATE_OBSERVER")
    model = RewriteModel()
    with pytest.raises(GenerationCancelled):
        await run_prepare(diagnostic_settings, model, tracer=BrokenTracer(), cancel=GenerationCancelled())
    assert model.rewrite_calls == []
    update, _ = await run_prepare(diagnostic_settings, model, tracer=BrokenTracer())
    assert update["preparation_diagnostics"]["rewriteStatus"] == "ready"
    assert "PRIVATE_OBSERVER" not in caplog.text


@pytest.mark.asyncio
async def test_logger_failure_does_not_mask_success(diagnostic_settings, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_LOGGER")
    monkeypatch.setattr("ai_service.orchestration.nodes.preparation.logger.info", broken)
    update, _ = await run_prepare(diagnostic_settings, RewriteModel())
    assert update["preparation_diagnostics"]["rewriteStatus"] == "ready"


@pytest.mark.asyncio
async def test_observer_projection_failure_preserves_original_terminal(diagnostic_settings, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("PRIVATE_PROJECTION")
    monkeypatch.setattr("ai_service.orchestration.nodes.preparation._observe", broken)
    update, _ = await run_prepare(diagnostic_settings, RewriteModel())
    assert update["preparation_diagnostics"]["rewriteStatus"] == "ready"
    with pytest.raises(GenerationCancelled):
        await run_prepare(diagnostic_settings, RewriteModel(error=GenerationCancelled()))


def test_checkpoint_and_trace_filter_untrusted_diagnostic_values():
    diagnostic = {"rewriteStatus": "ready", "reason": "NONE", "durationMs": 2.5,
                  "modelAttempted": True, "briefBytes": 10,
                  "skillIds": ["design-core@1", "PRIVATE_SKILL", "design-core@PRIVATE_VERSION"],
                  "prompt": "PRIVATE_PROMPT"}
    payload = GenerationWorkflow._checkpoint_payload({"preparation_diagnostics": diagnostic}, "generation_prepare")
    assert payload["preparation_diagnostics"]["skillIds"] == ["design-core@1"]
    assert "PRIVATE_" not in str(payload)
    assert "preparation_diagnostics" not in GenerationInput.__annotations__
    assert "preparation_diagnostics" not in VerificationInput.__annotations__
    assert _safe_metadata({"preparation_status": "PRIVATE_STATUS", "preparation_reason": "PRIVATE_REASON",
                           "preparation_duration_ms": float("nan"), "preparation_attempted": "PRIVATE_BOOL",
                           "preparation_brief_bytes": -1, "preparation_skill_ids": "design-core@1,PRIVATE_SKILL"}) == {
                               "preparation_skill_ids": "design-core@1"}
    for key, value in [("durationMs", float("inf")), ("durationMs", -1), ("reason", ["NONE"]),
                       ("briefBytes", True), ("modelAttempted", 1)]:
        assert safe_preparation_diagnostics({**diagnostic, key: value}) is None


@pytest.mark.asyncio
async def test_reuse_does_not_repeat_model_or_send_diagnostic_to_source(diagnostic_settings):
    model = RewriteModel()
    update, _ = await run_prepare(diagnostic_settings, model)
    state = {**update, "prompt": "保留图片", "code_gen_type": "HTML"}
    repeated, _ = await run_prepare(diagnostic_settings, model, state=state)
    assert len(model.rewrite_calls) == 1 and "preparation_diagnostics" not in str(model.rewrite_calls)
    assert repeated["preparation_diagnostics"]["modelAttempted"] is False
    assert repeated["preparation_diagnostics"]["rewriteStatus"] == "ready"


@pytest.mark.asyncio
async def test_sdk_provider_failure_has_no_retry_or_raw_log(diagnostic_settings, caplog):
    caplog.set_level(logging.INFO)
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(500, json={"error": {"message": "PRIVATE_PROVIDER_RESPONSE"}})
    model, http = make_http_model(diagnostic_settings, handler)
    async with http:
        update, _ = await run_prepare(diagnostic_settings, model)
    assert len(requests) == 1
    assert update["preparation_diagnostics"]["reason"] == "PROVIDER_ERROR"
    assert "PRIVATE_PROVIDER_RESPONSE" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout_type", [httpx.ConnectTimeout, httpx.ReadTimeout])
async def test_sdk_http_timeout_is_classified_without_retry(diagnostic_settings, caplog, timeout_type):
    caplog.set_level(logging.INFO)
    requests = []
    def handler(request):
        requests.append(request)
        raise timeout_type("PRIVATE_TIMEOUT_RESPONSE", request=request)
    model, http = make_http_model(diagnostic_settings, handler)
    async with http:
        update, events = await run_prepare(diagnostic_settings, model)
    diagnostic = update["preparation_diagnostics"]
    assert diagnostic["rewriteStatus"] == "fallback" and diagnostic["reason"] == "TIMEOUT"
    assert diagnostic["modelAttempted"] is True and len(requests) == 1
    assert "PRIVATE_TIMEOUT_RESPONSE" not in caplog.text + str(events)


@pytest.mark.asyncio
async def test_async_task_cancellation_is_logged_and_still_propagates(diagnostic_settings, caplog):
    caplog.set_level(logging.INFO)
    started = asyncio.Event()
    class PendingModel:
        async def rewrite_requirements(self, context):
            started.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(run_prepare(diagnostic_settings, PendingModel()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    diagnostic = logged_diagnostics(caplog)[0]
    assert diagnostic["rewriteStatus"] == "cancelled" and diagnostic["modelAttempted"] is True


@pytest.mark.asyncio
async def test_trace_emits_once_with_only_validated_scalars(diagnostic_settings):
    records = []
    class Tracer:
        async def record(self, **kwargs):
            records.append(kwargs)
    update, _ = await run_prepare(diagnostic_settings, RewriteModel(), tracer=Tracer())
    assert len(records) == 1
    metadata = records[0]["metadata"]
    assert _safe_metadata(metadata) == metadata
    assert metadata["preparation_status"] == "ready"
    assert metadata["preparation_duration_ms"] == update["preparation_diagnostics"]["durationMs"]
    assert "PRIVATE_METADATA" not in str(records) and "保留图片" not in str(records)
    records.clear()
    settings = diagnostic_settings.model_copy(update={"preparation_diagnostics_enabled": False})
    await run_prepare(settings, RewriteModel(), tracer=Tracer())
    assert records == []


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
def test_workflow_keeps_diagnostics_out_of_generation_and_repair_inputs(app_factory, diagnostic_settings, branch):
    from fastapi.testclient import TestClient
    from conftest import parse_ndjson

    model = RewriteModel(reviews=[False, True])
    app = app_factory(model=model)
    app.state.workflow.settings = diagnostic_settings
    with TestClient(app) as client:
        response = client.post("/internal/v1/generations:stream", headers={"Authorization": "Bearer test-secret"},
                               json={"appId": "diagnostic", "requestId": "no-leak", "prompt": "保留图片",
                                     "codeGenType": branch})
    events = parse_ndjson(response)
    assert events[-1]["type"] == "completed"
    assert "preparation_diagnostics" not in str(model.calls) + str(model.rewrite_calls) + str(events)
    assert "durationMs" not in str(events)
