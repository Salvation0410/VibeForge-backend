from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ai_service.config import Settings


@dataclass(slots=True)
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelTurn:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class GenerationModel(Protocol):
    async def route(self, prompt: str) -> str: ...

    async def generate(self, branch: str, context: dict[str, Any]) -> ModelTurn: ...

    async def review(self, artifact: str, context: dict[str, Any]) -> bool: ...

    async def repair(self, artifact: str, context: dict[str, Any]) -> str: ...


class OpenAICompatibleModel:
    """DeepSeek by default; any OpenAI-compatible endpoint can be configured."""

    def __init__(self, settings: Settings):
        self._client = ChatOpenAI(
            api_key=settings.model_api_key,
            base_url=settings.model_base_url,
            model=settings.model_name,
            temperature=settings.model_temperature,
        )

    async def route(self, prompt: str) -> str:
        response = await self._client.ainvoke(
            [
                SystemMessage(
                    content=(
                        "Classify the requested output. Reply with exactly one of: "
                        "HTML, MULTI_FILE, VUE_PROJECT."
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        return str(response.content).strip().upper()

    async def generate(self, branch: str, context: dict[str, Any]) -> ModelTurn:
        tool_note = (
            "For VUE_PROJECT, you may request a Spring-owned tool using strict JSON: "
            '{"content":"...","toolCalls":[{"name":"search_reference","arguments":{}}]}. '
            "Otherwise return the artifact as content with an empty toolCalls list."
        )
        response = await self._client.ainvoke(
            [
                SystemMessage(content=f"Generate a {branch} application. {tool_note}"),
                HumanMessage(content=json.dumps(context, ensure_ascii=False)),
            ]
        )
        raw = str(response.content)
        if branch != "VUE_PROJECT":
            return ModelTurn(content=raw)
        try:
            payload = json.loads(_strip_json_fence(raw))
            calls = [
                ToolCall(name=item["name"], arguments=item.get("arguments", {}))
                for item in payload.get("toolCalls", [])
            ]
            return ModelTurn(content=payload.get("content", ""), tool_calls=calls)
        except (json.JSONDecodeError, KeyError, TypeError):
            return ModelTurn(content=raw)

    async def review(self, artifact: str, context: dict[str, Any]) -> bool:
        response = await self._client.ainvoke(
            [
                SystemMessage(content="Review the artifact. Reply only PASS or REPAIR."),
                HumanMessage(content=json.dumps({"artifact": artifact, **context}, ensure_ascii=False)),
            ]
        )
        return str(response.content).strip().upper() == "PASS"

    async def repair(self, artifact: str, context: dict[str, Any]) -> str:
        response = await self._client.ainvoke(
            [
                SystemMessage(content="Repair the artifact using the review context. Return only the artifact."),
                HumanMessage(content=json.dumps({"artifact": artifact, **context}, ensure_ascii=False)),
            ]
        )
        return str(response.content)


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline >= 0 else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()

