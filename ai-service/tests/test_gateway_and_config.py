import json

import httpx
import pytest
from pydantic import ValidationError

from ai_service.config import Settings
from ai_service.infrastructure.checkpoint import RedisCheckpoint
from ai_service.infrastructure.spring_tools import (
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
        return httpx.Response(200, json={"code": 0, "data": {"result": "ok"}, "message": "ok"})

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
    assert result == {"result": "ok"}
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
                "data": {"published": True, "versionId": "req-1"},
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
        {"codeGenType": "MULTI_FILE"},
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
            {"codeGenType": "MULTI_FILE"},
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
async def test_spring_gateway_keeps_legacy_dict_response_compatibility():
    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": {"result": "legacy-ok"}})
        ),
    )

    result = await gateway.invoke(
        "project_build",
        {"codeGenType": "VUE_PROJECT"},
        app_id="42",
        request_id="req-1",
        tool_call_id="req-1:build:1",
    )

    assert result == {"result": "legacy-ok"}
    await gateway.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected_code"),
    [
        ("TOOL_EXECUTION_INDETERMINATE", "TOOL_EXECUTION_INDETERMINATE"),
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


def test_security_tokens_must_be_configured():
    with pytest.raises(ValidationError):
        Settings(
            internal_bearer_token="",
            spring_gateway_base_url="http://spring.test",
            spring_gateway_bearer_token="",
        )


@pytest.mark.asyncio
async def test_optional_redis_has_explicit_degraded_mode(monkeypatch):
    checkpoint = RedisCheckpoint("redis://127.0.0.1:1/0", required=False, ttl_seconds=86400)

    async def unavailable():
        raise OSError("redis unavailable")

    monkeypatch.setattr(checkpoint._client, "ping", unavailable)
    await checkpoint.start()
    assert checkpoint.available is False
    await checkpoint.save("42:req", {"node": "input_guard"})
    assert checkpoint.get_graph_saver() is None


def test_available_redis_exposes_a_real_langgraph_checkpointer():
    checkpoint = RedisCheckpoint("redis://localhost:6379/0", required=False, ttl_seconds=86400)
    checkpoint.available = True
    assert checkpoint.get_graph_saver() is not None
    assert checkpoint.get_graph_saver().__class__.__mro__[1].__name__ == "BaseCheckpointSaver"


@pytest.mark.asyncio
async def test_required_redis_fails_startup(monkeypatch):
    checkpoint = RedisCheckpoint("redis://127.0.0.1:1/0", required=True, ttl_seconds=86400)

    async def unavailable():
        raise OSError("redis unavailable")

    monkeypatch.setattr(checkpoint._client, "ping", unavailable)
    with pytest.raises(RuntimeError, match="Redis checkpoint is required"):
        await checkpoint.start()
