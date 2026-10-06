from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from ai_service.config import Settings
from ai_service.models.base import (
    CustomerServiceContext,
    CustomerServiceModelAnswer,
    ModelTurn,
    ToolCall,
)
from ai_service.models.quality_review import (
    QualityReviewOutputError,
    ReviewerResult,
    ReviewerRole,
)
from ai_service.models.tool_contract import vue_tool_prompt
from ai_service.prompts import (
    CUSTOMER_SERVICE_SYSTEM_PROMPT,
    QUALITY_REVIEW_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    ROUTING_SYSTEM_PROMPT,
    generation_system_prompt,
    quality_review_system_prompt,
    customer_service_user_prompt,
)


class CustomerServiceModelOutputError(RuntimeError):
    """Stable failure for malformed or ungrounded answer-model output."""

    def __init__(self):
        self.code = "CUSTOMER_SERVICE_MODEL_INVALID_OUTPUT"
        super().__init__(self.code)


class _CustomerServiceAnswerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    answered: StrictBool
    answer: str = Field(max_length=4_000)
    cited_chunk_ids: list[str] = Field(alias="citedChunkIds", max_length=3)


class OpenAICompatibleModel:
    """基于 LangChain ChatOpenAI 的模型适配器，默认连接 DeepSeek 兼容接口。"""

    def __init__(self, settings: Settings):
        self._customer_service_prompt_max_bytes = settings.rag_prompt_max_bytes
        self._client = ChatOpenAI(
            api_key=settings.model_api_key,
            base_url=settings.model_base_url,
            model=settings.model_name,
            temperature=settings.model_temperature,
            max_tokens=settings.model_max_tokens,
        )

    def health_ready(self) -> bool:
        """Report local adapter availability without sending a model request."""

        return getattr(self, "_client", None) is not None

    async def route(self, prompt: str) -> str:
        """要求模型返回唯一的生成类型标识。"""
        response = await self._invoke(
            [
                SystemMessage(content=ROUTING_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        return str(response.content).strip().upper()

    async def generate(self, branch: str, context: dict[str, Any]) -> ModelTurn:
        """生成代码产物，并解析 Vue 分支返回的结构化工具调用。"""
        generation_instructions = generation_system_prompt(branch)
        if branch == "VUE_PROJECT":
            generation_instructions = f"{generation_instructions}\n\n{vue_tool_prompt()}"
        response = await self._invoke(
            [
                SystemMessage(content=generation_instructions),
                HumanMessage(content=json.dumps(context, ensure_ascii=False)),
            ]
        )
        raw = str(response.content)
        if branch != "VUE_PROJECT":
            return _model_turn(response, raw)
        return _vue_model_turn(response)

    async def review(self, artifact: str, context: dict[str, Any]) -> bool:
        """让模型以 PASS 或 REPAIR 判断产物是否通过质量检查。"""
        response = await self._invoke(
            [
                SystemMessage(content=QUALITY_REVIEW_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps({"artifact": artifact, **context}, ensure_ascii=False)),
            ]
        )
        return str(response.content).strip().upper() == "PASS"

    async def review_role(
        self,
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        """让指定角色返回严格校验的结构化质量审查结果。"""
        response = await self._invoke(
            [
                SystemMessage(content=quality_review_system_prompt(role.value)),
                HumanMessage(content=json.dumps({"artifact": artifact, **context}, ensure_ascii=False)),
            ]
        )
        raw = str(response.content)
        try:
            result = ReviewerResult.model_validate_json(_strip_json_fence(raw))
        except (ValidationError, json.JSONDecodeError):
            raise QualityReviewOutputError("invalid_json") from None
        if result.reviewer is not role:
            raise QualityReviewOutputError("identity_mismatch")
        return result

    async def repair(self, artifact: str, context: dict[str, Any]) -> ModelTurn:
        """结合验证和构建上下文生成修复后的完整产物。"""
        repair_instructions = REPAIR_SYSTEM_PROMPT
        if context.get("codeGenType") == "VUE_PROJECT":
            repair_instructions = f"{repair_instructions}\n\n{vue_tool_prompt()}"
        response = await self._invoke(
            [
                SystemMessage(content=repair_instructions),
                HumanMessage(content=json.dumps({"artifact": artifact, **context}, ensure_ascii=False)),
            ]
        )
        if context.get("codeGenType") == "VUE_PROJECT":
            return _vue_model_turn(response)
        return _model_turn(response, str(response.content))

    async def answer_customer_service(
        self,
        question: str,
        contexts: list[CustomerServiceContext],
    ) -> CustomerServiceModelAnswer:
        """Generate and strictly validate a grounded single-turn answer."""

        response = await self._client.ainvoke([
            SystemMessage(content=CUSTOMER_SERVICE_SYSTEM_PROMPT),
            HumanMessage(content=customer_service_user_prompt(
                question, contexts,
                max_bytes=self._customer_service_prompt_max_bytes,
            )),
        ])
        try:
            payload = _CustomerServiceAnswerPayload.model_validate_json(
                str(response.content).strip()
            )
        except (ValidationError, json.JSONDecodeError):
            raise CustomerServiceModelOutputError() from None

        provided_ids = {item.chunk_id for item in contexts}
        citations = payload.cited_chunk_ids
        valid_citations = (
            all(isinstance(item, str) and item and item in provided_ids for item in citations)
            and len(citations) == len(set(citations))
        )
        if payload.answered:
            if not payload.answer.strip() or not citations or not valid_citations:
                raise CustomerServiceModelOutputError()
        elif payload.answer != "" or citations:
            raise CustomerServiceModelOutputError()
        return CustomerServiceModelAnswer(
            answered=bool(payload.answered),
            answer=payload.answer,
            cited_chunk_ids=tuple(citations),
        )


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


def _vue_model_turn(response: Any) -> ModelTurn:
    """解析 Vue 模型的 JSON 文本与工具调用，格式异常时保留原始响应。"""
    raw = str(response.content)
    try:
        payload = json.loads(_strip_json_fence(raw))
        calls = [
            ToolCall(name=item["name"], arguments=item.get("arguments", {}))
            for item in payload.get("toolCalls", [])
        ]
        return _model_turn(response, payload.get("content", ""), calls)
    except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
        return _model_turn(response, raw)


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline >= 0 else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()

