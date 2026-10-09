"""生成与类型路由共享的规则检查、语义审核和故障处理。"""

import asyncio
import logging
import unicodedata
from typing import Any

from ai_service.config import Settings
from ai_service.models.input_review import InputReviewError, InputReviewResult, check_context_budget

logger = logging.getLogger(__name__)


class InputReviewer:
    def __init__(self, model: Any, settings: Settings):
        self.model = model
        self.settings = settings

    async def review(self, prompt: str, *, request_id: str, code_gen_type: str | None = None,
                     conversation: list[dict[str, Any]] | None = None,
                     metadata: dict[str, Any] | None = None) -> InputReviewResult:
        if not prompt.strip():
            raise InputReviewError("INPUT_EMPTY", "请输入应用创建或修改需求。")
        if len(prompt) > self.settings.input_prompt_max_chars:
            raise InputReviewError("INPUT_TOO_LONG", f"需求过长，请控制在 {self.settings.input_prompt_max_chars} 个字符内或拆分为多次修改。")
        if any(unicodedata.category(char) in {"Cc", "Cs"} and char not in "\n\r\t" for char in prompt):
            raise InputReviewError("INPUT_INVALID", "输入包含异常控制字符，请删除这些字符后重试。")
        # 当前输入永不截断；历史仅保留有界的用户需求，工具输出和系统角色不作为审核指令。
        history = [item for item in (conversation or []) if item.get("role") == "user"]
        selected = history[-6:]
        history_truncated = len(history) > len(selected)
        bounded_history = []
        for item in selected:
            content = str(item.get("content", ""))
            history_truncated |= len(content) > 1000
            bounded_history.append({"role": "user", "content": content[:1000]})
        metadata = metadata or {}
        initial_prompt = str(metadata.get("initialPrompt", ""))
        context = {
            "prompt": prompt, "conversation": bounded_history,
            "historyTruncated": history_truncated or bool(metadata.get("historyTruncated")),
            "initialPrompt": initial_prompt[:2000],
            "initialPromptTruncated": len(initial_prompt) > 2000 or bool(metadata.get("initialPromptTruncated")),
            "existingProject": metadata.get("existingProject") is True,
            "codeGenType": code_gen_type,
            "capabilities": {"staticFrontend": True, "backendGeneration": False},
            "generationBudget": {"maxOutputTokens": self.settings.model_max_tokens,
                                 "maxVueToolCalls": self.settings.vue_max_tool_calls},
        }
        # 审核只看有限历史，但生成仍需检查收到的完整上下文，不能靠截断审核历史掩盖超预算。
        check_context_budget({"prompt": prompt, "conversation": conversation or [], "metadata": metadata},
                             context_tokens=self.settings.model_context_window_tokens,
                             output_tokens=self.settings.model_max_tokens,
                             overhead_tokens=self.settings.model_context_overhead_tokens)
        if not self.settings.input_review_enabled:
            return InputReviewResult(decision="ALLOW", reason="NONE", message="", questions=[])
        for attempt in range(self.settings.input_review_max_retries + 1):
            try:
                async with asyncio.timeout(self.settings.input_review_timeout_seconds):
                    result = await self.model.review_input(context)
                    # 注入的模型也必须遵守同一严格协议，不能把任意 dict 或字符串当作通过。
                    result = InputReviewResult.model_validate(result)
                logger.info("输入审核 request_id=%s decision=%s reason=%s attempt=%d",
                            request_id, result.decision, result.reason, attempt + 1)
                return self.enforce(result)
            except InputReviewError:
                raise
            except Exception:
                # 不记录模型原文、上游异常或请求正文，避免审核失败暴露隐私/凭据。
                logger.warning("输入审核不可用 request_id=%s attempt=%d", request_id, attempt + 1)
        raise InputReviewError("INPUT_REVIEW_UNAVAILABLE", "输入审核服务暂不可用，请稍后重试。本轮尚未开始生成。")

    @staticmethod
    def enforce(result: InputReviewResult) -> InputReviewResult:
        if result.decision == "REJECT":
            raise InputReviewError("INPUT_REJECTED", result.message)
        if result.decision == "CLARIFY":
            raise InputReviewError("INPUT_CLARIFICATION_REQUIRED", result.message + "\n" + "\n".join(result.questions))
        return result
