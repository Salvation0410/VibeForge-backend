"""输入审核协议与不含原始请求的稳定异常。"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InputReviewError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.user_message = message
        super().__init__(f"{code}: {message}")


class InputReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")

    decision: Literal["ALLOW", "ALLOW_WITH_WARNING", "CLARIFY", "REJECT"]
    reason: Literal["NONE", "SAFETY", "OUT_OF_SCOPE", "CONFLICT", "MISSING_CORE_DETAIL", "CAPABILITY", "SCALE"]
    message: str = Field(max_length=400)
    questions: list[str] = Field(max_length=2)

    @model_validator(mode="after")
    def validate_decision(self) -> "InputReviewResult":
        # 决策必须与原因相符，不能把需求过大或信息不足标记成安全违规。
        if any(not question.strip() or len(question) > 200 for question in self.questions):
            raise ValueError("审核问题无效")
        if self.decision == "ALLOW":
            if self.reason != "NONE" or self.message or self.questions:
                raise ValueError("正常放行不能携带拒绝或澄清理由")
        elif not self.message.strip():
            raise ValueError("非正常放行必须提供用户提示")
        if self.decision == "REJECT" and self.reason not in {"SAFETY", "OUT_OF_SCOPE"}:
            raise ValueError("拒绝原因无效")
        if self.decision == "CLARIFY":
            if self.reason not in {"CONFLICT", "MISSING_CORE_DETAIL", "CAPABILITY", "SCALE"} or not self.questions:
                raise ValueError("澄清必须提供可回答的问题")
        elif self.questions:
            raise ValueError("仅澄清决策可以提出问题")
        if self.decision == "ALLOW_WITH_WARNING" and self.reason not in {"CAPABILITY", "SCALE"}:
            raise ValueError("警告不能替代安全拦截或关键冲突澄清")
        return self


def check_context_budget(payload: object, *, context_tokens: int, output_tokens: int, overhead_tokens: int) -> None:
    """用 UTF-8 字节数作为保守 token 上界，不冒充供应商的精确 tokenizer。"""
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    try:
        upper_bound = len(serialized.encode("utf-8"))
    except UnicodeEncodeError:
        raise InputReviewError("INPUT_INVALID", "输入包含无法处理的字符，请检查后重试。") from None
    if upper_bound + output_tokens + overhead_tokens > context_tokens:
        raise InputReviewError(
            "INPUT_CONTEXT_BUDGET_EXCEEDED",
            "当前需求与上下文超过本轮模型输入预算。请缩短需求或分步修改；本轮不会继续生成。",
        )
