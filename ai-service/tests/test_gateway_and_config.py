import fnmatch
import json

import httpx
import pytest
from pydantic import ValidationError

from ai_service.config import Settings
from ai_service.infrastructure.checkpoint import RedisCheckpoint, RedisGraphSaver
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


@pytest.mark.asyncio
async def test_redis_graph_saver_deletes_only_target_thread_artifacts():
    all_keys = {
        "yu-ai:langgraph:checkpoint:NDI6cmVxLTE:_:cp-1",
        "yu-ai:langgraph:latest:NDI6cmVxLTE:_",
        "yu-ai:langgraph:writes:NDI6cmVxLTE:_:cp-1:task-0",
        "yu-ai:langgraph:checkpoint:NDI6b3RoZXI:_:cp-2",
        "yu-ai:langgraph:latest:OTHER:NDI6cmVxLTE:N",
    }
    deleted: list[str] = []

    class FakeRedis:
        async def scan_iter(self, *, match: str):
            for key in all_keys:
                if fnmatch.fnmatch(key, match):
                    yield key

        async def delete(self, *keys: str):
            deleted.extend(keys)

    saver = RedisGraphSaver(FakeRedis(), ttl_seconds=60)
    await saver.adelete_thread("42:req-1")

    assert set(deleted) == {
        "yu-ai:langgraph:checkpoint:NDI6cmVxLTE:_:cp-1",
        "yu-ai:langgraph:latest:NDI6cmVxLTE:_",
        "yu-ai:langgraph:writes:NDI6cmVxLTE:_:cp-1:task-0",
    }
    assert "yu-ai:langgraph:checkpoint:NDI6b3RoZXI:_:cp-2" not in deleted
    assert "yu-ai:langgraph:latest:OTHER:NDI6cmVxLTE:N" not in deleted


@pytest.mark.asyncio
async def test_redis_checkpoint_cleanup_delegates_when_available(monkeypatch):
    checkpoint = RedisCheckpoint("redis://localhost:6379/0", required=False, ttl_seconds=60)
    checkpoint.available = True
    cleaned: list[str] = []

    async def cleanup(thread_id: str):
        cleaned.append(thread_id)

    monkeypatch.setattr(checkpoint._graph_saver, "adelete_thread", cleanup)
    await checkpoint.cleanup_graph("42:req-1")

    assert cleaned == ["42:req-1"]
    assert checkpoint.available is True


@pytest.mark.asyncio
async def test_required_redis_cleanup_failure_degrades_without_raising(monkeypatch, caplog):
    checkpoint = RedisCheckpoint("redis://localhost:6379/0", required=True, ttl_seconds=60)
    checkpoint.available = True

    async def cleanup(_: str):
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(checkpoint._graph_saver, "adelete_thread", cleanup)
    with caplog.at_level("WARNING"):
        await checkpoint.cleanup_graph("42:req-1")

    assert checkpoint.available is False
    assert "LangGraph checkpoint cleanup failed for 42:req-1" in caplog.text
