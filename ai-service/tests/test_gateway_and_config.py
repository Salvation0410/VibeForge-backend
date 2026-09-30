import json

import httpx
import pytest
from pydantic import ValidationError

from ai_service.config import Settings
from ai_service.infrastructure.spring_tools import (
    InvalidSpringToolRequest,
    SpringToolError,
    SpringToolGateway,
    SpringToolProtocolError,
)


@pytest.mark.asyncio
async def test_spring_gateway_uses_bearer_and_scoped_tool_request():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"built": True, "errorCode": "", "message": ""},
                "message": "ok",
            },
        )

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )
    result = await gateway.invoke(
        "project_build",
        {"codeGenType": "VUE_PROJECT"},
        app_id="42",
        request_id="req-1",
        tool_call_id="req-1:build:1",
    )
    assert captured["authorization"] == "Bearer gateway-token"
    assert json.loads(captured["body"]) == {
        "appId": "42",
        "requestId": "req-1",
        "toolCallId": "req-1:build:1",
        "toolName": "project_build",
        "arguments": {"codeGenType": "VUE_PROJECT"},
    }
    assert result == {"built": True, "errorCode": "", "message": ""}
    await gateway.close()


@pytest.mark.asyncio
async def test_spring_gateway_validates_vue_source_snapshot_response():
    snapshot = {
        "files": [{"path": "src/App.vue", "content": "<template>fixed</template>", "truncated": False}],
        "eligibleFileCount": 1,
        "includedFileCount": 1,
        "omittedFileCount": 0,
        "truncated": False,
    }
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read()))
        return httpx.Response(200, json={"code": 0, "data": snapshot, "message": "ok"})

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )

    result = await gateway.invoke(
        "vue_source_snapshot",
        {"codeGenType": "VUE_PROJECT"},
        app_id="42",
        request_id="req-1",
        tool_call_id="req-1:vue-source-snapshot:1",
    )

    assert captured["toolName"] == "vue_source_snapshot"
    assert captured["arguments"] == {"codeGenType": "VUE_PROJECT"}
    assert result == snapshot
    await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [
        {
            "files": [{"path": "src/App.vue", "content": "x" * 12_001, "truncated": True}],
            "eligibleFileCount": 1,
            "includedFileCount": 1,
            "omittedFileCount": 0,
            "truncated": True,
        },
        {
            "files": [{"path": "src/App.vue", "content": "fixed", "truncated": False}],
            "eligibleFileCount": 1,
            "includedFileCount": 1,
            "truncated": False,
        },
    ],
)
async def test_spring_gateway_rejects_invalid_vue_source_snapshot_response(snapshot):
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": snapshot, "message": "ok"})
        ),
    )

    with pytest.raises(SpringToolProtocolError):
        await gateway.invoke(
            "vue_source_snapshot",
            {"codeGenType": "VUE_PROJECT"},
            app_id="42",
            request_id="req-1",
            tool_call_id="req-1:vue-source-snapshot:1",
        )
    await gateway.close()


@pytest.mark.asyncio
async def test_project_build_uses_extended_read_timeout_only_for_build_tool():
    timeouts = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        timeouts[body["toolName"]] = request.extensions["timeout"]
        data = (
            {"built": True, "errorCode": "", "message": ""}
            if body["toolName"] == "project_build"
            else {"content": "<template />"}
        )
        return httpx.Response(200, json={"code": 0, "data": data, "message": "ok"})

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )
    arguments_by_name = {
        "project_build": {"codeGenType": "VUE_PROJECT"},
        "file_read": {
            "relativeFilePath": "src/App.vue",
            "codeGenType": "VUE_PROJECT",
        },
    }
    for name in ("project_build", "file_read"):
        await gateway.invoke(
            name,
            arguments_by_name[name],
            app_id="42",
            request_id="req-timeout",
            tool_call_id=f"req-timeout:{name}",
        )

    assert timeouts["project_build"]["read"] > 1020
    assert timeouts["project_build"]["connect"] == 30
    assert timeouts["project_build"]["write"] == 30
    assert timeouts["project_build"]["pool"] == 30
    assert timeouts["file_read"] == {
        "connect": 30.0,
        "read": 30.0,
        "write": 30.0,
        "pool": 30.0,
    }
    await gateway.close()


@pytest.mark.asyncio
async def test_artifact_publish_retries_lost_response_with_same_tool_call_id():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read()))
        if len(requests) == 1:
            raise httpx.ReadError("response lost after Spring committed", request=request)
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "published": True,
                    "versionId": "req-1",
                    "hashes": {"index.html": "abc"},
                },
                "message": "ok",
            },
        )

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )

    result = await gateway.invoke(
        "artifact_publish",
        {
            "artifact": "candidate",
            "codeGenType": "MULTI_FILE",
            "engine": "langgraph",
            "finishReason": "STOP",
        },
        app_id="42",
        request_id="req-1",
        tool_call_id="req-1:artifact_publish",
    )

    assert result["published"] is True
    assert [request["toolCallId"] for request in requests] == [
        "req-1:artifact_publish",
        "req-1:artifact_publish",
    ]
    assert all(request["appId"] == "42" for request in requests)
    assert all(request["requestId"] == "req-1" for request in requests)
    await gateway.close()


@pytest.mark.asyncio
async def test_artifact_publish_does_not_retry_explicit_spring_business_error():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read()))
        return httpx.Response(
            200,
            json={
                "code": 50001,
                "data": None,
                "message": "TOOL_EXECUTION_INDETERMINATE",
            },
        )

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SpringToolError) as exc_info:
        await gateway.invoke(
            "artifact_publish",
            {
                "artifact": "candidate",
                "codeGenType": "MULTI_FILE",
                "engine": "langgraph",
                "finishReason": "STOP",
            },
            app_id="42",
            request_id="req-1",
            tool_call_id="req-1:artifact_publish",
        )

    assert len(requests) == 1
    assert exc_info.value.spring_code == 50001
    assert exc_info.value.spring_message == "TOOL_EXECUTION_INDETERMINATE"
    assert str(exc_info.value).startswith("TOOL_EXECUTION_INDETERMINATE:")
    assert "code=50001" in str(exc_info.value)
    await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"code": True, "data": {}},
        {"code": "0", "data": {}},
        {"code": 0},
        {"code": 0, "data": None},
        {"code": 0, "data": []},
        {"code": 0, "data": "C:/private/generated-project"},
        {"data": []},
        {"error": "failed", "detail": "C:/private/generated-project"},
        {"data": {"result": "legacy-ok"}, "extra": "C:/private/generated-project"},
        {},
        [],
        "C:/private/generated-project",
        42,
    ],
)
async def test_spring_gateway_rejects_malformed_response_schema(payload):
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=payload)
        ),
    )

    with pytest.raises(SpringToolProtocolError) as exc_info:
        await gateway.invoke(
            "project_build",
            {"codeGenType": "VUE_PROJECT"},
            app_id="42",
            request_id="req-1",
            tool_call_id="req-1:build:1",
        )

    assert str(exc_info.value) == (
        "SPRING_TOOL_PROTOCOL_ERROR: Spring tool response did not match expected schema"
    )
    assert "private" not in str(exc_info.value)
    await gateway.close()


@pytest.mark.asyncio
async def test_spring_gateway_keeps_exact_legacy_data_envelope_compatibility():
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "data": {"built": True, "errorCode": "", "message": ""}
                },
            )
        ),
    )

    result = await gateway.invoke(
        "project_build",
        {"codeGenType": "VUE_PROJECT"},
        app_id="42",
        request_id="req-1",
        tool_call_id="req-1:build:1",
    )

    assert result == {"built": True, "errorCode": "", "message": ""}
    await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        ("TOOL_EXECUTION_INDETERMINATE", "TOOL_EXECUTION_INDETERMINATE"),
        ("TOOL_IDEMPOTENCY_CONFLICT", "TOOL_IDEMPOTENCY_CONFLICT"),
        ("TOOL_EXECUTION_BUSY", "TOOL_EXECUTION_BUSY"),
        ("TOOL_IDEMPOTENCY_UNAVAILABLE", "TOOL_IDEMPOTENCY_UNAVAILABLE"),
        ("INTERNAL_SECRET_TOKEN", "SPRING_TOOL_ERROR"),
        ("INVALID_RELATIVE_PATH", "SPRING_TOOL_ERROR"),
        (None, "SPRING_TOOL_ERROR"),
        ("", "SPRING_TOOL_ERROR"),
        ("   ", "SPRING_TOOL_ERROR"),
        (123, "SPRING_TOOL_ERROR"),
        ("failed at C:/private/generated-project", "SPRING_TOOL_ERROR"),
        ("ordinary failure message", "SPRING_TOOL_ERROR"),
    ],
)
async def test_spring_business_error_message_is_sanitized(message, expected_code):
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={"code": 50001, "data": None, "message": message},
            )
        ),
    )

    with pytest.raises(SpringToolError) as exc_info:
        await gateway.invoke(
            "project_build",
            {"codeGenType": "VUE_PROJECT"},
            app_id="42",
            request_id="req-1",
            tool_call_id="req-1:build:1",
        )

    assert exc_info.value.spring_message == message
    assert str(exc_info.value) == (
        f"{expected_code}: Spring tool request failed (code=50001)"
    )
    assert "private" not in str(exc_info.value)
    assert "ordinary failure" not in str(exc_info.value)
    await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "expected_requests"),
    [
        ("project_build", 1),
        ("artifact_publish", 2),
    ],
)
async def test_invalid_json_becomes_protocol_error_after_bounded_retry(
    tool_name, expected_requests
):
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            content="C:/private/raw-response-body",
            headers={"content-type": "application/json"},
        )

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SpringToolProtocolError) as exc_info:
        await gateway.invoke(
            tool_name,
            (
                {
                    "artifact": "candidate",
                    "codeGenType": "MULTI_FILE",
                    "engine": "langgraph",
                    "finishReason": "STOP",
                }
                if tool_name == "artifact_publish"
                else {"codeGenType": "MULTI_FILE"}
            ),
            app_id="42",
            request_id="req-1",
            tool_call_id=f"req-1:{tool_name}",
        )

    assert request_count == expected_requests
    assert str(exc_info.value) == (
        "SPRING_TOOL_PROTOCOL_ERROR: Spring tool response did not match expected schema"
    )
    assert "private" not in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValueError)
    await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        (
            "file_read",
            {
                "relativeFilePath": "src/private.vue",
                "codeGenType": "VUE_PROJECT",
                "appId": "forged-secret-app",
            },
        ),
        ("file_read", {"codeGenType": "VUE_PROJECT"}),
        (
            "file_read",
            {"relativeFilePath": 42, "codeGenType": "VUE_PROJECT"},
        ),
        ("unknown_private_tool", {}),
        ("artifact_publish", {"codeGenType": "MULTI_FILE"}),
    ],
)
async def test_invalid_tool_request_fails_before_http_with_sanitized_error(
    tool_name, arguments
):
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={})

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(InvalidSpringToolRequest) as exc_info:
        await gateway.invoke(
            tool_name,
            arguments,
            app_id="42",
            request_id="req-secret",
            tool_call_id="req-secret:tool",
        )

    assert request_count == 0
    assert str(exc_info.value) == (
        "INVALID_SPRING_TOOL_REQUEST: Spring tool request did not match expected schema"
    )
    assert "private" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)
    assert exc_info.value.__cause__ is not None
    await gateway.close()


@pytest.mark.asyncio
async def test_project_build_result_allows_unknown_extension_fields():
    data = {"built": True, "errorCode": "", "message": "", "durationMs": 125}
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": data})
        ),
    )

    result = await gateway.invoke(
        "project_build",
        {"codeGenType": "VUE_PROJECT"},
        app_id="42",
        request_id="req-1",
        tool_call_id="req-1:build:1",
    )

    assert result == data
    await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "data",
    [
        {"built": "yes-private", "errorCode": "", "message": "secret"},
        {"built": True, "errorCode": "private"},
    ],
)
async def test_invalid_success_result_becomes_sanitized_protocol_error(data):
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": data})
        ),
    )

    with pytest.raises(SpringToolProtocolError) as exc_info:
        await gateway.invoke(
            "project_build",
            {"codeGenType": "VUE_PROJECT"},
            app_id="42",
            request_id="req-1",
            tool_call_id="req-1:build:1",
        )

    assert str(exc_info.value) == (
        "SPRING_TOOL_PROTOCOL_ERROR: Spring tool response did not match expected schema"
    )
    assert "private" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)
    assert "yes" not in str(exc_info.value)
    assert exc_info.value.__cause__ is not None
    await gateway.close()


@pytest.mark.asyncio
async def test_invalid_legacy_envelope_result_is_still_validated():
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": {"built": "private"}})
        ),
    )

    with pytest.raises(SpringToolProtocolError):
        await gateway.invoke(
            "project_build",
            {"codeGenType": "VUE_PROJECT"},
            app_id="42",
            request_id="req-1",
            tool_call_id="req-1:build:1",
        )
    await gateway.close()


@pytest.mark.asyncio
async def test_invalid_artifact_publish_result_is_not_retried():
    request_count = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {"published": True, "versionId": "release-private"},
            },
        )

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(SpringToolProtocolError):
        await gateway.invoke(
            "artifact_publish",
            {
                "artifact": "secret source",
                "codeGenType": "MULTI_FILE",
                "engine": "langgraph",
                "finishReason": "STOP",
            },
            app_id="42",
            request_id="req-1",
            tool_call_id="req-1:artifact_publish",
        )

    assert request_count == 1
    await gateway.close()


def test_security_tokens_must_be_configured():
    with pytest.raises(ValidationError):
        Settings(
            internal_bearer_token="",
            spring_gateway_base_url="http://spring.test",
            spring_gateway_bearer_token="",
        )


def test_model_max_tokens_defaults_to_8192_and_must_be_positive():
    settings = Settings(
        internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
    )

    assert settings.model_max_tokens == 8192

    with pytest.raises(ValidationError):
        Settings(
            internal_bearer_token="internal-token",
            spring_gateway_base_url="http://spring.test",
            spring_gateway_bearer_token="gateway-token",
            model_max_tokens=0,
        )


def test_checkpoint_postgres_defaults():
    settings = Settings(
        internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
    )

    assert settings.checkpoint_enabled is True
    assert settings.checkpoint_required is False
    assert settings.checkpoint_postgres_url.startswith("postgresql://")
    assert settings.checkpoint_auto_setup is True
    assert settings.checkpoint_pool_min_size == 1
    assert settings.checkpoint_pool_max_size == 5


def test_checkpoint_pool_max_must_not_be_smaller_than_min():
    with pytest.raises(ValidationError, match="checkpoint_pool_max_size"):
        Settings(
            internal_bearer_token="internal-token",
            spring_gateway_base_url="http://spring.test",
            spring_gateway_bearer_token="gateway-token",
            checkpoint_pool_min_size=4,
            checkpoint_pool_max_size=2,
        )


def test_multi_agent_review_defaults_to_disabled_with_sixty_second_timeout(monkeypatch):
    monkeypatch.delenv("AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED", raising=False)
    monkeypatch.delenv("AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS", raising=False)

    settings = Settings(
        _env_file=None,
        internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
    )

    assert settings.multi_agent_review_enabled is False
    assert settings.multi_agent_review_timeout_seconds == 60.0


@pytest.mark.parametrize("timeout", [0, -1, 301])
def test_multi_agent_review_timeout_rejects_out_of_range_values(timeout):
    with pytest.raises(ValidationError, match="multi_agent_review_timeout_seconds"):
        Settings(
            _env_file=None,
            internal_bearer_token="internal-token",
            spring_gateway_base_url="http://spring.test",
            spring_gateway_bearer_token="gateway-token",
            multi_agent_review_timeout_seconds=timeout,
        )


@pytest.mark.parametrize("timeout", [0.1, 300])
def test_multi_agent_review_timeout_accepts_boundary_values(timeout):
    settings = Settings(
        _env_file=None,
        internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
        multi_agent_review_timeout_seconds=timeout,
    )

    assert settings.multi_agent_review_timeout_seconds == timeout


def test_multi_agent_review_config_parses_string_constructor_values():
    settings = Settings(
        _env_file=None,
        internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
        multi_agent_review_enabled="true",
        multi_agent_review_timeout_seconds="12.5",
    )

    assert settings.multi_agent_review_enabled is True
    assert settings.multi_agent_review_timeout_seconds == 12.5


def test_multi_agent_review_config_uses_ai_service_environment_prefix(monkeypatch):
    monkeypatch.setenv("AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED", "true")
    monkeypatch.setenv("AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS", "45.5")

    settings = Settings(
        _env_file=None,
        internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
    )

    assert settings.multi_agent_review_enabled is True
    assert settings.multi_agent_review_timeout_seconds == 45.5


def _rag_settings(**overrides):
    env_file = overrides.pop("_env_file", None)
    return Settings(
        _env_file=env_file,
        internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
        **overrides,
    )


def test_customer_service_rag_defaults_are_disabled_and_bounded(monkeypatch):
    for name in (
        "AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED",
        "CLOSEAI_API_KEY",
        "CLOSEAI_BASE_URL",
        "AI_SERVICE_CLOSEAI_API_KEY",
        "AI_SERVICE_CLOSEAI_BASE_URL",
        "AI_SERVICE_MILVUS_URI",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = _rag_settings()

    assert settings.customer_service_rag_enabled is False
    assert settings.closeai_api_key == ""
    assert settings.closeai_base_url == ""
    assert settings.rag_chunk_size == 1000
    assert settings.rag_chunk_overlap == 150
    assert settings.rag_retrieval_top_k == 8
    assert settings.rag_final_top_k == 3
    assert settings.rag_min_rerank_score is None
    assert settings.milvus_uri == "http://localhost:19530"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"rag_chunk_size": 150, "rag_chunk_overlap": 150}, "rag_chunk_overlap"),
        ({"rag_chunk_size": 100, "rag_chunk_overlap": 150}, "rag_chunk_overlap"),
        ({"rag_retrieval_top_k": 2, "rag_final_top_k": 3}, "rag_final_top_k"),
    ],
)
def test_customer_service_rag_rejects_inconsistent_bounds(overrides, field):
    with pytest.raises(ValidationError, match=field):
        _rag_settings(**overrides)


@pytest.mark.parametrize("missing", ["milvus_uri", "closeai_api_key", "closeai_base_url"])
def test_customer_service_rag_requires_connections_only_when_enabled(missing):
    required = {
        "milvus_uri": "http://localhost:19530",
        "closeai_api_key": "test-closeai-key",
        "closeai_base_url": "https://closeai.test/v1",
    }
    required[missing] = ""

    with pytest.raises(ValidationError, match=missing):
        _rag_settings(customer_service_rag_enabled=True, **required)


def test_customer_service_rag_local_reranker_requires_cuda():
    with pytest.raises(ValidationError, match="rag_reranker_device"):
        _rag_settings(rag_reranker_provider="local_cross_encoder", rag_reranker_device="cpu")


def test_customer_service_rag_accepts_legacy_closeai_environment(monkeypatch):
    monkeypatch.setenv("CLOSEAI_API_KEY", "legacy-key")
    monkeypatch.setenv("CLOSEAI_BASE_URL", "https://legacy.test/v1")
    monkeypatch.delenv("AI_SERVICE_CLOSEAI_API_KEY", raising=False)
    monkeypatch.delenv("AI_SERVICE_CLOSEAI_BASE_URL", raising=False)

    settings = _rag_settings(customer_service_rag_enabled=True)

    assert settings.closeai_api_key == "legacy-key"
    assert settings.closeai_base_url == "https://legacy.test/v1"


def test_customer_service_rag_prefixed_closeai_environment_takes_priority(monkeypatch):
    monkeypatch.setenv("CLOSEAI_API_KEY", "legacy-key")
    monkeypatch.setenv("CLOSEAI_BASE_URL", "https://legacy.test/v1")
    monkeypatch.setenv("AI_SERVICE_CLOSEAI_API_KEY", "prefixed-key")
    monkeypatch.setenv("AI_SERVICE_CLOSEAI_BASE_URL", "https://prefixed.test/v1")

    settings = _rag_settings(customer_service_rag_enabled=True)

    assert settings.closeai_api_key == "prefixed-key"
    assert settings.closeai_base_url == "https://prefixed.test/v1"


def test_customer_service_rag_prefixed_closeai_dotenv_takes_priority(monkeypatch, tmp_path):
    for name in (
        "CLOSEAI_API_KEY",
        "CLOSEAI_BASE_URL",
        "AI_SERVICE_CLOSEAI_API_KEY",
        "AI_SERVICE_CLOSEAI_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / "rag.env"
    env_file.write_text(
        "CLOSEAI_API_KEY=legacy-key\n"
        "CLOSEAI_BASE_URL=https://legacy.test/v1\n"
        "AI_SERVICE_CLOSEAI_API_KEY=prefixed-key\n"
        "AI_SERVICE_CLOSEAI_BASE_URL=https://prefixed.test/v1\n",
        encoding="utf-8",
    )

    settings = _rag_settings(_env_file=env_file, customer_service_rag_enabled=True)

    assert settings.closeai_api_key == "prefixed-key"
    assert settings.closeai_base_url == "https://prefixed.test/v1"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"rag_chunk_size": 0}, "rag_chunk_size"),
        ({"rag_chunk_overlap": -1}, "rag_chunk_overlap"),
        ({"rag_retrieval_top_k": 0}, "rag_retrieval_top_k"),
        ({"rag_final_top_k": 0}, "rag_final_top_k"),
        ({"rag_embedding_batch_size": 0}, "rag_embedding_batch_size"),
        ({"rag_max_embedding_elements": 0}, "rag_max_embedding_elements"),
        ({"rag_etl_max_concurrency": 0}, "rag_etl_max_concurrency"),
        ({"rag_reranker_batch_size": 0}, "rag_reranker_batch_size"),
        ({"rag_reranker_timeout_seconds": 0}, "rag_reranker_timeout_seconds"),
        ({"rag_download_max_bytes": 0}, "rag_download_max_bytes"),
        ({"rag_download_connect_timeout_seconds": 0}, "rag_download_connect_timeout_seconds"),
        ({"rag_download_read_timeout_seconds": 0}, "rag_download_read_timeout_seconds"),
        ({"rag_min_rerank_score": float("nan")}, "rag_min_rerank_score"),
    ],
)
def test_customer_service_rag_rejects_invalid_numeric_settings(overrides, field):
    with pytest.raises(ValidationError, match=field):
        _rag_settings(**overrides)


def test_customer_service_rag_nonlocal_reranker_can_use_cpu():
    settings = _rag_settings(rag_reranker_provider="disabled", rag_reranker_device="cpu")

    assert settings.rag_reranker_device == "cpu"
