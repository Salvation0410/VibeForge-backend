from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ai_service.app import create_app
from ai_service.config import Settings
from ai_service.llm import ModelTurn, ToolCall


class FakeModel:
    def __init__(self, *, reviews: list[bool] | None = None, vue_tool_calls: int = 0):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.reviews = list(reviews or [True])
        self.vue_tool_calls = vue_tool_calls
        self._vue_turn = 0

    async def route(self, prompt: str) -> str:
        self.calls.append(("route", {"prompt": prompt}))
        lowered = prompt.lower()
        if "vue" in lowered:
            return "VUE_PROJECT"
        if "multiple" in lowered or "multi" in lowered:
            return "MULTI_FILE"
        return "HTML"

    async def generate(self, branch: str, context: dict[str, Any]) -> ModelTurn:
        self.calls.append(("generate", {"branch": branch, "context": context}))
        if branch == "VUE_PROJECT" and self._vue_turn < self.vue_tool_calls:
            self._vue_turn += 1
            return ModelTurn(
                content=f"vue-step-{self._vue_turn}",
                tool_calls=[ToolCall(name="search_reference", arguments={"q": "layout"})],
            )
        return ModelTurn(content=f"artifact:{branch}")

    async def review(self, artifact: str, context: dict[str, Any]) -> bool:
        self.calls.append(("review", {"artifact": artifact, "context": context}))
        return self.reviews.pop(0) if self.reviews else False

    async def repair(self, artifact: str, context: dict[str, Any]) -> str:
        self.calls.append(("repair", {"artifact": artifact, "context": context}))
        return artifact + "|repaired"


class FakeToolGateway:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def invoke(self, name: str, arguments: dict[str, Any], *, tool_call_id: str) -> dict[str, Any]:
        call = {"name": name, "arguments": arguments, "toolCallId": tool_call_id}
        self.calls.append(call)
        return {"ok": True, "echo": call}


class MemoryCheckpoint:
    available = True

    def __init__(self):
        self.saved: dict[str, list[dict[str, Any]]] = defaultdict(list)

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def save(self, thread_id: str, state: dict[str, Any]) -> None:
        self.saved[thread_id].append(dict(state))

    async def ping(self) -> bool:
        return self.available


@pytest.fixture
def settings() -> Settings:
    return Settings(
        internal_bearer_token="test-secret",
        spring_gateway_base_url="http://spring.test/api/internal/ai-tools",
        spring_gateway_bearer_token="spring-secret",
        redis_enabled=False,
        redis_required=False,
    )


@pytest.fixture
def app_factory(settings: Settings):
    def factory(*, model=None, gateway=None, checkpoint=None):
        return create_app(
            settings=settings,
            model=model or FakeModel(),
            tool_gateway=gateway or FakeToolGateway(),
            checkpoint=checkpoint or MemoryCheckpoint(),
        )

    return factory


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer test-secret"}


def parse_ndjson(response) -> list[dict[str, Any]]:
    return [json.loads(line) for line in response.text.splitlines() if line]


@pytest.fixture
def ndjson_parser():
    return parse_ndjson

