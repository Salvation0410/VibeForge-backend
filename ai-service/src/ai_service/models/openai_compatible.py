from __future__ import annotations

import json
from contextlib import aclosing
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from openai import APITimeoutError
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

from ai_service.config import Settings
from ai_service.models.input_review import InputReviewError, InputReviewResult, check_context_budget
from ai_service.models.vue_context import project_vue_context, visible_file_fingerprints
from ai_service.models.vue_review_context import vue_review_context
from ai_service.prompts.review import VUE_REVIEW_EVIDENCE_INSTRUCTIONS
from ai_service.models.generation_brief import GenerationBrief
from ai_service.models.preparation_diagnostics import PreparationFailure
from ai_service.models.single_quality_review import SingleReviewOutputError, SingleReviewResult, parse_single_review
from ai_service.prompts.preparation import REWRITE_SYSTEM_PROMPT
from ai_service.prompts.frontend_skills import frontend_instructions
from ai_service.prompts.input_review import INPUT_REVIEW_SYSTEM_PROMPT
from ai_service.models.base import (
    CustomerServiceContext,
    CustomerServiceModelAnswer,
    ModelTurn,
    TextDeltaCallback,
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
    """回答格式错误或引用不属于输入知识时，返回稳定错误码。"""

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
        self._settings = settings
        self._customer_service_prompt_max_bytes = settings.rag_prompt_max_bytes
        self._client = ChatOpenAI(
            api_key=settings.model_api_key,
            base_url=settings.model_base_url,
            model=settings.model_name,
            temperature=settings.model_temperature,
            max_tokens=settings.model_max_tokens,
        )

    def health_ready(self) -> bool:
        """仅检查本地适配器，不向供应商发送健康探测请求。"""

        return getattr(self, "_client", None) is not None

    def _output_tokens(self, generation=False):
        settings = getattr(self, "_settings", None)
        if settings is None:
            return None
        return getattr(settings, "generation_max_tokens", settings.model_max_tokens) if generation else settings.model_max_tokens

    def _check_budget(self, messages, *, generation=False):
        # 当前产物、历史及工具结果会继续增长，实际模型调用前再次检查完整载荷。
        settings = getattr(self, "_settings", None)
        if settings is not None:
            check_context_budget(
                [{"role": message.type, "content": message.content} for message in messages],
                context_tokens=settings.model_context_window_tokens,
                output_tokens=self._output_tokens(generation),
                overhead_tokens=settings.model_context_overhead_tokens,
            )

    async def _invoke(self, messages, *, response_format: Literal["json_object"] | None = None, generation=False):
        self._check_budget(messages, generation=generation)
        # 请求级覆盖避免污染共享客户端，生成与客服可并发使用不同输出额度。
        options = {"max_tokens": self._output_tokens(True)} if generation and self._output_tokens(True) is not None else {}
        with tracing_context(enabled=False):
            if response_format == "json_object":
                # 格式只约束本次请求，不能修改共享客户端而污染并发生成调用。
                return await self._client.ainvoke(messages, response_format={"type": "json_object"}, **options)
            return await self._client.ainvoke(messages, **options)

    def _vue_messages(self, instructions: str, context: dict[str, Any], *, generation=False, visibility=None):
        """以实际消息预算选择工具历史，完整生成/修复状态不随模型窗口裁剪。"""
        def messages(payload):
            return [SystemMessage(content=instructions),
                    HumanMessage(content=json.dumps(payload, ensure_ascii=False))]

        def fits(payload):
            try:
                self._check_budget(messages(payload), generation=generation)
                return True
            except InputReviewError as exc:
                if exc.code != "INPUT_CONTEXT_BUDGET_EXCEEDED":
                    raise
                return False

        projected = project_vue_context(context, fits=fits)
        if visibility is not None:
            visibility.update(visible_file_fingerprints(projected))
        return messages(projected)

    async def _stream_static_turn(self, messages, on_delta: TextDeltaCallback) -> ModelTurn:
        """聚合完整响应才返回候选；任务取消、回调异常和供应商失败均关闭响应流。"""
        self._check_budget(messages, generation=True)
        options = {"max_tokens": self._output_tokens(True)} if self._output_tokens(True) is not None else {}
        combined = None
        with tracing_context(enabled=False):
            async with aclosing(self._client.astream(messages, stream_usage=True, **options)) as stream:
                async for chunk in stream:
                    combined = chunk if combined is None else combined + chunk
                    text = chunk.text()
                    if text:
                        await on_delta(text)
        if combined is None:
            raise ValueError("MODEL_STREAM_INCOMPLETE: model returned an empty stream")
        if combined.tool_calls:
            raise ValueError("MODEL_STREAM_UNEXPECTED_TOOL_CALLS: static text cannot execute tools")
        return _model_turn(combined, combined.text())

    async def generate_stream(self, branch: str, context: dict[str, Any], *,
                              on_delta: TextDeltaCallback) -> ModelTurn:
        """静态分支文本可增量展示；Vue 工具 JSON 始终使用完整响应入口。"""
        if branch not in {"HTML", "MULTI_FILE"}:
            raise ValueError("STATIC_STREAM_UNSUPPORTED: only static artifacts support text deltas")
        return await self._stream_static_turn([
            SystemMessage(content=generation_system_prompt(branch) + frontend_instructions(context)),
            HumanMessage(content=json.dumps(context, ensure_ascii=False)),
        ], on_delta)

    async def repair_stream(self, artifact: str, context: dict[str, Any], *,
                            on_delta: TextDeltaCallback) -> ModelTurn:
        """静态修复与首次生成使用相同的增量/完整候选契约。"""
        if context.get("codeGenType") not in {"HTML", "MULTI_FILE"}:
            raise ValueError("STATIC_STREAM_UNSUPPORTED: Vue repair requires complete tool JSON")
        return await self._stream_static_turn([
            SystemMessage(content=REPAIR_SYSTEM_PROMPT + frontend_instructions(context)),
            HumanMessage(content=json.dumps({"artifact": artifact, **context}, ensure_ascii=False)),
        ], on_delta)

    async def review_input(self, context: dict[str, Any]) -> InputReviewResult:
        """独立审核调用只允许短结构化响应，截断、工具调用及无效 JSON 均不能放行。"""
        messages = [SystemMessage(content=INPUT_REVIEW_SYSTEM_PROMPT),
                    HumanMessage(content=json.dumps(context, ensure_ascii=False))]
        check_context_budget(
            [{"role": message.type, "content": message.content} for message in messages],
            context_tokens=self._settings.model_context_window_tokens,
            output_tokens=1024,
            overhead_tokens=self._settings.model_context_overhead_tokens,
        )
        with tracing_context(enabled=False):
            response = await self._client.bind(max_tokens=1024, temperature=0).ainvoke(messages)
        metadata = getattr(response, "response_metadata", {}) or {}
        finish_reason = str(metadata.get("finish_reason") or metadata.get("stop_reason") or "").upper()
        if finish_reason in {"LENGTH", "MAX_TOKENS", "CONTENT_FILTER", "CONTENT_FILTERED"} or getattr(response, "tool_calls", None):
            raise ValueError("审核响应不完整")
        # 禁止容忍围栏、额外字段和类型转换，防止模型自由文本被误当作审核成功。
        return InputReviewResult.model_validate_json(str(response.content).strip())

    async def rewrite_requirements(self, context: dict[str, Any]) -> GenerationBrief:
        """复用异步 HTTP 客户端，但在 SDK 层关闭重试；绑定请求参数不能关闭 SDK 重试。"""
        # 重写与生成解析同一组本地规则，不把源数据中的自由文本提升为系统指令。
        rules = frontend_instructions({"generationPreparation": {"skills": context.get("selectedSkills", [])}})
        messages = [SystemMessage(content=REWRITE_SYSTEM_PROMPT + rules),
                    HumanMessage(content=json.dumps(context, ensure_ascii=False))]
        check_context_budget(
            [{"role": message.type, "content": message.content} for message in messages],
            context_tokens=self._settings.model_context_window_tokens,
            output_tokens=self._settings.prompt_rewrite_max_tokens,
            overhead_tokens=self._settings.model_context_overhead_tokens,
        )
        root = self._client.root_async_client.with_options(max_retries=0)
        client = self._client.model_copy(update={
            "root_async_client": root, "async_client": root.chat.completions, "max_retries": 0,
        })
        with tracing_context(enabled=False):
            try:
                response = await client.bind(max_tokens=self._settings.prompt_rewrite_max_tokens,
                                             temperature=0).ainvoke(messages)
            except APITimeoutError:
                # SDK 将 HTTP 超时封装为独立异常类型，需显式归类且不保留请求或异常正文。
                raise PreparationFailure("TIMEOUT") from None
        metadata = getattr(response, "response_metadata", {}) or {}
        reason = str(metadata.get("finish_reason") or metadata.get("stop_reason") or "").upper()
        if reason not in {"STOP", "END_TURN"} or getattr(response, "tool_calls", None) or getattr(response, "invalid_tool_calls", None):
            raise PreparationFailure("INCOMPLETE_RESPONSE")
        if not isinstance(response.content, str):
            raise PreparationFailure("INVALID_SCHEMA")
        if len(response.content.encode("utf-8")) > self._settings.prompt_rewrite_max_bytes:
            raise PreparationFailure("BRIEF_TOO_LARGE")
        try:
            return GenerationBrief.model_validate_json(response.content)
        except ValidationError as exc:
            # 只读取错误类型，不保留包含供应商输出的异常正文或字段值。
            reason = "INVALID_JSON" if any(item["type"] == "json_invalid" for item in exc.errors()) else "INVALID_SCHEMA"
            raise PreparationFailure(reason) from None

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
        generation_instructions = generation_system_prompt(branch) + frontend_instructions(context)
        if branch == "VUE_PROJECT":
            generation_instructions = f"{generation_instructions}\n\n{vue_tool_prompt()}"
        visibility = {}
        messages = self._vue_messages(generation_instructions, context, generation=True, visibility=visibility) if branch == "VUE_PROJECT" else [
            SystemMessage(content=generation_instructions),
            HumanMessage(content=json.dumps(context, ensure_ascii=False)),
        ]
        response = await self._invoke(messages, generation=True)
        raw = str(response.content)
        if branch != "VUE_PROJECT":
            return _model_turn(response, raw)
        turn = _vue_model_turn(response)
        turn.vue_visible_files = visibility
        return turn

    async def review(self, artifact: str, context: dict[str, Any]) -> SingleReviewResult:
        """单次调用返回结构化判定与拒绝依据，非法响应不触发额外调用。"""
        is_vue = context.get("codeGenType") == "VUE_PROJECT"
        if is_vue:
            context = vue_review_context(context)
        payload = {**context, "artifact": artifact}
        # 正式 Vue 审查以当前快照为准；旧的无类型调用仍保留工具历史投影兼容。
        instructions = QUALITY_REVIEW_SYSTEM_PROMPT + frontend_instructions(context)
        if is_vue:
            instructions += "\n\n" + VUE_REVIEW_EVIDENCE_INSTRUCTIONS
        messages = self._vue_messages(instructions, payload) if "toolResults" in context else [
            SystemMessage(content=instructions),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
        response = await self._invoke(messages)
        raw = str(response.content)
        metadata = getattr(response, "response_metadata", None)
        reason = (metadata.get("finish_reason") or metadata.get("stop_reason")) if isinstance(metadata, dict) else None
        reason = reason.upper() if isinstance(reason, str) else "UNKNOWN"
        result = None
        output_reason = "UNCLASSIFIED"
        try:
            result = parse_single_review(raw, reason)
            output_reason = "NONE"
            return result
        except SingleReviewOutputError as exc:
            output_reason = exc.output_reason
            raise
        finally:
            # 自由文本理由只进入内部修复载荷；日志只记录可信枚举与计数。
            try:
                import logging

                logging.getLogger(__name__).info(
                    "quality_review_result verdict=%s responseChars=%d finishReason=%s issueCount=%d issueCodes=%s outputReason=%s",
                    result.verdict if result else "INVALID_OUTPUT", len(raw),
                    reason if reason in {"STOP", "LENGTH", "CONTENT_FILTER", "MAX_TOKENS", "CONTENT_FILTERED"} else "UNKNOWN",
                    len(result.issues) if result else 0,
                    ",".join(sorted({issue.code for issue in result.issues})) if result else "",
                    output_reason,
                )
            except Exception:
                # 日志故障不覆盖解析异常；调用异常和取消在此边界之外传播。
                pass

    async def review_role(
        self,
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        """让指定角色返回严格校验的结构化质量审查结果。"""
        response = await self._invoke(
            [
                SystemMessage(content=quality_review_system_prompt(role.value) + frontend_instructions(context)),
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
        repair_instructions = REPAIR_SYSTEM_PROMPT + frontend_instructions(context)
        if context.get("codeGenType") == "VUE_PROJECT":
            repair_instructions = f"{repair_instructions}\n\n{generation_system_prompt('VUE_PROJECT')}\n\n{vue_tool_prompt()}"
        payload = {"artifact": artifact, **context}
        visibility = {}
        messages = self._vue_messages(repair_instructions, payload, generation=True, visibility=visibility) if context.get("codeGenType") == "VUE_PROJECT" else [
            SystemMessage(content=repair_instructions),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
        ]
        response = await self._invoke(messages, generation=True)
        if context.get("codeGenType") == "VUE_PROJECT":
            turn = _vue_model_turn(response)
            turn.vue_visible_files = visibility
            return turn
        return _model_turn(response, str(response.content))

    async def answer_customer_service(
        self,
        question: str,
        contexts: list[CustomerServiceContext],
    ) -> CustomerServiceModelAnswer:
        """基于本次知识片段生成单轮回答，并严格校验字段、引用及拒答协议。"""

        messages = [
            SystemMessage(content=CUSTOMER_SERVICE_SYSTEM_PROMPT),
            HumanMessage(content=customer_service_user_prompt(
                question, contexts,
                max_bytes=self._customer_service_prompt_max_bytes,
            )),
        ]
        if getattr(getattr(self, "_settings", None), "rag_answer_response_format", "text") == "json_object":
            response = await self._invoke(messages, response_format="json_object")
        else:
            # 保留默认及无配置注入模型的旧调用签名，不向旧覆写方法添加关键字。
            response = await self._invoke(messages)
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
    """仅解析完整 JSON 对象；格式异常返回标记，不展示或执行原始源码。"""
    raw = str(response.content)
    try:
        payload = json.loads(_strip_json_fence(raw))
        if not isinstance(payload, dict) or not isinstance(payload.get("content", ""), str):
            raise TypeError("invalid Vue response object")
        items = payload.get("toolCalls", [])
        plan = payload.get("filePlan", [])
        if not isinstance(items, list) or not isinstance(plan, list):
            raise TypeError("invalid Vue response lists")
        if len(plan) > 40 or not all(isinstance(path, str) and 0 < len(path) <= 256 for path in plan):
            raise TypeError("invalid Vue file plan")
        if any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
               or not item["name"] or not isinstance(item.get("arguments", {}), dict) for item in items):
            raise TypeError("invalid Vue tool structure")
        calls = [
            ToolCall(name=item["name"], arguments=item.get("arguments", {}))
            for item in items
        ]
        turn = _model_turn(response, payload.get("content", ""), calls)
        turn.file_plan = list(dict.fromkeys(plan))
        return turn
    except (json.JSONDecodeError, AttributeError, KeyError, TypeError):
        turn = _model_turn(response, "")
        turn.vue_output_invalid = True
        return turn


def _strip_json_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```"):
        first_newline = stripped.find("\n")
        stripped = stripped[first_newline + 1 :] if first_newline >= 0 else stripped
        if stripped.endswith("```"):
            stripped = stripped[:-3]
    return stripped.strip()

