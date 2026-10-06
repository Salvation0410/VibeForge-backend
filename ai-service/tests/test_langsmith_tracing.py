from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace

import pytest

from ai_service.config import Settings
from ai_service.infrastructure.langsmith_tracing import (
    LangSmithTracer, _safe_metadata, _install_sdk_log_redaction,
)


def _settings(**overrides):
    values = {
        "_env_file": None,
        "internal_bearer_token": "internal-token",
        "spring_gateway_base_url": "http://spring.test",
        "spring_gateway_bearer_token": "gateway-token",
        "langsmith_tracing": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_langsmith_settings_are_read_from_existing_env_names(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://smith.example.com")
    monkeypatch.setenv("LANGSMITH_API_KEY", "secret")
    monkeypatch.setenv("LANGSMITH_PROJECT", "project")
    settings = Settings(
        _env_file=None, internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
    )
    assert settings.langsmith_tracing is True
    assert str(settings.langsmith_endpoint) == "https://smith.example.com/"
    assert settings.langsmith_api_key == "secret"
    assert settings.langsmith_project == "project"


def test_disabled_tracer_does_not_create_client():
    tracer = LangSmithTracer(_settings())
    assert tracer.enabled is False
    assert tracer._client is None


def test_metadata_filter_excludes_prompt_artifact_and_tool_values():
    safe = _safe_metadata({
        "request_id": "request-1", "app_id": "app-1", "prompt": "secret prompt",
        "artifact": "secret source", "tool_arguments": {"path": "secret"},
        "status": "completed", "repair_count": float("nan"),
    })
    assert safe == {"request_id": "request-1", "app_id": "app-1", "status": "completed"}


def _enabled_tracer(monkeypatch, client_type, **kwargs):
    import langsmith
    monkeypatch.setattr(langsmith, "Client", client_type)
    return LangSmithTracer(_settings(
        langsmith_tracing=True, langsmith_endpoint="https://smith.example.com",
        langsmith_api_key="secret",
    ), **kwargs)


class CapturingClient:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.runs = []
        self.closed = False

    def create_run(self, **kwargs):
        self.runs.append(kwargs)

    def close(self, *, timeout):
        assert timeout == 0
        self.closed = True


@pytest.mark.asyncio
async def test_only_safe_payload_is_submitted_with_real_start_and_end(monkeypatch):
    tracer = _enabled_tracer(monkeypatch, CapturingClient)
    started = datetime.now(UTC) - timedelta(seconds=5)
    metadata = {"request_id": "r1", "repair_count": 2, "prompt": "private", "artifact": "source"}
    await tracer.record(name="generation", metadata=metadata, status="failed",
                        error_code="GENERATION_FAILED", started_at=started)
    metadata["request_id"] = "mutated"
    await tracer.close()
    client = tracer._client
    assert client.closed
    assert client.options["auto_batch_tracing"] is False
    assert client.options["timeout_ms"] == (1000, 1000)
    run = client.runs[0]
    assert run["inputs"] == {}
    assert run["start_time"] == started
    assert run["end_time"] >= started + timedelta(seconds=5)
    assert run["dotted_order"].startswith(started.strftime("%Y%m%dT%H%M%S"))
    assert run["extra"]["metadata"] == {
        "request_id": "r1", "repair_count": 2, "status": "failed", "error_code": "GENERATION_FAILED",
    }
    assert "private" not in repr(run) and "source" not in repr(run)


@pytest.mark.asyncio
async def test_tracing_failure_is_isolated_and_redacted(monkeypatch, caplog):
    class FailingClient(CapturingClient):
        def create_run(self, **kwargs):
            raise RuntimeError("provider-secret")

    tracer = _enabled_tracer(monkeypatch, FailingClient)
    with caplog.at_level(logging.WARNING):
        await tracer.record(name="generation", metadata={}, status="failed")
        await tracer.close()
    assert tracer._client.closed
    assert "RuntimeError" in caplog.text
    assert "provider-secret" not in caplog.text


def test_sdk_log_redaction_covers_existing_and_new_loggers(caplog):
    _install_sdk_log_redaction()
    with caplog.at_level(logging.WARNING):
        try:
            raise RuntimeError("trace-response-secret")
        except RuntimeError:
            logging.getLogger("langsmith._internal._background_thread").error("raw-response-secret", exc_info=True)
        logging.getLogger("langsmith.new_logger_after_install").warning("url-with-secret=%s", "key-secret", stack_info=True)
        logging.getLogger("ai_service.test").warning("ordinary business warning")
    assert "ordinary business warning" in caplog.text
    assert "RuntimeError" in caplog.text
    assert "secret" not in caplog.text
    assert "Traceback" not in caplog.text


@pytest.mark.asyncio
async def test_slow_sender_has_bounded_queue_and_close_and_no_business_wait(monkeypatch):
    entered, release = Event(), Event()

    class SlowClient(CapturingClient):
        def create_run(self, **kwargs):
            entered.set()
            release.wait(5)
            super().create_run(**kwargs)

    tracer = _enabled_tracer(monkeypatch, SlowClient, queue_size=2, shutdown_timeout=0.05)
    try:
        await asyncio.wait_for(tracer.record(name="generation", metadata={}, status="completed"), timeout=0.1)
        for _ in range(100):
            if entered.is_set():
                break
            await asyncio.sleep(0.005)
        assert entered.is_set()
        for index in range(50):
            await asyncio.wait_for(tracer.record(name="node", metadata={"repair_count": index}, status="completed"), timeout=0.1)
        assert tracer._queue.qsize() == 2
        await asyncio.wait_for(tracer.close(), timeout=0.3)
        await tracer.record(name="ignored", metadata={}, status="completed")
        assert tracer._queue.qsize() == 2
    finally:
        release.set()
        await asyncio.wait_for(tracer.close(), timeout=0.3)
    assert len(tracer._client.runs) == 1
    assert tracer._client.closed


@pytest.mark.asyncio
async def test_cancelled_close_still_closes_worker(monkeypatch):
    release = Event()

    class SlowClient(CapturingClient):
        def create_run(self, **kwargs):
            release.wait(5)

    tracer = _enabled_tracer(monkeypatch, SlowClient)
    await tracer.record(name="generation", metadata={}, status="completed")
    task = asyncio.create_task(tracer.close())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert tracer._closed.is_set()
    release.set()
    await tracer.close()
    assert tracer._client.closed


@pytest.fixture
def automatic_tracing(monkeypatch):
    from langchain_core.callbacks import manager
    from langchain_core.callbacks.base import BaseCallbackHandler
    from langsmith import utils, tracing_context

    class FakeTracer(BaseCallbackHandler):
        def __init__(self, **kwargs):
            pass

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    utils.get_env_var.cache_clear()
    monkeypatch.setattr(manager, "LangChainTracer", FakeTracer)
    with tracing_context(enabled=True):
        assert any(isinstance(handler, FakeTracer) for handler in manager.AsyncCallbackManager.configure().handlers)
        yield manager
    utils.get_env_var.cache_clear()


@pytest.mark.asyncio
async def test_route_model_suppresses_automatic_callbacks(automatic_tracing):
    from ai_service.models.openai_compatible import OpenAICompatibleModel

    class FakeChatClient:
        async def ainvoke(self, messages):
            assert automatic_tracing.AsyncCallbackManager.configure().handlers == []
            return SimpleNamespace(content="HTML")

    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model._client = FakeChatClient()
    assert await model.route("private prompt") == "HTML"


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario,status,repairs,tools", [
    ("repair", "completed", 1, 0), ("vue", "completed", 0, 2),
    ("failed", "failed", 2, 0), ("cancelled", "cancelled", 0, 0),
])
async def test_graph_suppresses_callbacks_and_records_actual_terminal(automatic_tracing, scenario, status, repairs, tools):
    from ai_service.api.schemas import GenerationRequest
    from ai_service.orchestration.cancellation import CancellationRegistry
    from ai_service.orchestration.workflow import GenerationWorkflow
    from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint

    class CheckedModel(FakeModel):
        async def generate(self, branch, context):
            assert automatic_tracing.AsyncCallbackManager.configure().handlers == []
            return await super().generate(branch, context)

    class RecordingTracer:
        def __init__(self):
            self.records = []

        async def record(self, **kwargs):
            self.records.append(kwargs)

    cancellations = CancellationRegistry()
    tracer = RecordingTracer()
    checkpoint = MemoryCheckpoint()
    reviews = [False, True] if scenario == "repair" else [False] * 3 if scenario == "failed" else [True]
    workflow = GenerationWorkflow(
        model=CheckedModel(reviews=reviews, vue_tool_calls=tools), tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint, cancellations=cancellations, settings=_settings(), tracer=tracer,
    )
    if scenario == "cancelled":
        cancellations.cancel("app:req")
    events = await workflow.run(GenerationRequest(
        request_id="req", app_id="app", prompt="private",
        code_gen_type="VUE_PROJECT" if scenario == "vue" else "HTML",
    ))
    final = tracer.records[-1]
    assert final["name"] == "generation"
    assert final["status"] == status
    assert final["metadata"]["repair_count"] == repairs
    assert final["metadata"]["tool_call_count"] == tools
    assert final["started_at"] <= datetime.now(UTC)
    expected_error = None if status == "completed" else "cancelled" if status == "cancelled" else "GENERATION_FAILED"
    assert final["error_code"] == expected_error
    assert checkpoint.cleaned_graph_threads == ["app:req"]
    assert events[-1].type == ("completed" if status == "completed" else "failed")
