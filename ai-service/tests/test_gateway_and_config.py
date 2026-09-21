import json

import httpx
import pytest
from pydantic import ValidationError

from ai_service.config import Settings
from ai_service.infrastructure.checkpoint import RedisCheckpoint
from ai_service.infrastructure.spring_tools import SpringToolGateway


@pytest.mark.asyncio
async def test_spring_gateway_uses_bearer_and_tool_call_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"data": {"result": "ok"}})

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )
    result = await gateway.invoke("project_build", {"appId": "42"}, tool_call_id="req:build:1")
    assert captured["authorization"] == "Bearer gateway-token"
    assert '"toolCallId":"req:build:1"' in captured["body"]
    assert result == {"result": "ok"}
    await gateway.close()


@pytest.mark.asyncio
async def test_artifact_publish_retries_lost_response_with_same_tool_call_id():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.read()))
        if len(requests) == 1:
            raise httpx.ReadError("response lost after Spring committed", request=request)
        return httpx.Response(200, json={"data": {"published": True, "versionId": "req-1"}})

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )

    result = await gateway.invoke(
        "artifact_publish",
        {"appId": "42", "requestId": "req-1"},
        tool_call_id="req-1:artifact_publish",
    )

    assert result["published"] is True
    assert [request["toolCallId"] for request in requests] == [
        "req-1:artifact_publish",
        "req-1:artifact_publish",
    ]
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
