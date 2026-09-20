from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from ai_service.config import Settings
from ai_service.models.base import ModelTurn, ToolCall


class OpenAICompatibleModel:
    """基于 LangChain ChatOpenAI 的模型适配器，默认连接 DeepSeek 兼容接口。"""

    def __init__(self, settings: Settings):
        self._client = ChatOpenAI(
            api_key=settings.model_api_key,
            base_url=settings.model_base_url,
            model=settings.model_name,
            temperature=settings.model_temperature,
        )

    async def route(self, prompt: str) -> str:
        """要求模型返回唯一的生成类型标识。"""
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
        """生成代码产物，并解析 Vue 分支返回的结构化工具调用。"""
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
            return _model_turn(response, raw)
        try:
            payload = json.loads(_strip_json_fence(raw))
            calls = [
                ToolCall(name=item["name"], arguments=item.get("arguments", {}))
                for item in payload.get("toolCalls", [])
            ]
            return _model_turn(response, payload.get("content", ""), calls)
        except (json.JSONDecodeError, KeyError, TypeError):
            return _model_turn(response, raw)

    async def review(self, artifact: str, context: dict[str, Any]) -> bool:
        """让模型以 PASS 或 REPAIR 判断产物是否通过质量检查。"""
        response = await self._client.ainvoke(
            [
                SystemMessage(content="Review the artifact. Reply only PASS or REPAIR."),
                HumanMessage(content=json.dumps({"artifact": artifact, **context}, ensure_ascii=False)),
            ]
        )
        return str(response.content).strip().upper() == "PASS"

    async def repair(self, artifact: str, context: dict[str, Any]) -> ModelTurn:
        """结合验证和构建上下文生成修复后的完整产物。"""
        response = await self._client.ainvoke(
            [
                SystemMessage(content="Repair the artifact using the review context. Return only the artifact."),
                HumanMessage(content=json.dumps({"artifact": artifact, **context}, ensure_ascii=False)),
            ]
        )
        return _model_turn(response, str(response.content))


def _model_turn(response: Any, content: str, tool_calls: list[ToolCall] | None = None) -> ModelTurn:
    """把供应商响应规范化为工作流使用的内容、结束原因和 token 用量。"""
    metadata = getattr(response, "response_metadata", {}) or {}
    finish_reason = metadata.get("finish_reason") or metadata.get("stop_reason")
    usage = getattr(response, "usage_metadata", {}) or {}
    normalized_usage = {str(key): int(value) for key, value in usage.items() if isinstance(value, int)}
    return ModelTurn(
        content=content,
        tool_calls=list(tool_calls or []),
        finish_reason=str(finish_reason).upper() if finish_reason is not None else None,
        token_usage=normalized_usage,
    )


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline >= 0 else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()

