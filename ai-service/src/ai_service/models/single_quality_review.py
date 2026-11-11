"""单 Reviewer 的有界诊断契约；模型文字不是系统指令或确定性验收证据。"""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


IssueCode = Literal[
    "REQUIREMENT_MISSING", "FUNCTIONAL_ERROR", "TECHNICAL_ERROR", "SCOPE_VIOLATION",
]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SingleReviewIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    code: IssueCode
    summary: Annotated[Text, Field(max_length=160)]
    evidence: Annotated[Text, Field(max_length=240)]
    repair_hint: Annotated[Text, Field(max_length=240)]


class SingleReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: Literal["PASS", "REPAIR"]
    issues: Annotated[list[SingleReviewIssue], Field(max_length=4)]

    @model_validator(mode="after")
    def consistent_verdict(self):
        if (self.verdict == "PASS") != (len(self.issues) == 0):
            raise ValueError("判定与问题列表不一致")
        return self

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    @property
    def repair_feedback(self) -> str:
        # JSON 保留字段边界；反馈始终处于用户载荷，不拼入系统提示。
        return "" if self.passed else self.model_dump_json()


class SingleReviewOutputError(ValueError):
    def __init__(self, *, incomplete: bool = False):
        super().__init__("QUALITY_REVIEW_INCOMPLETE" if incomplete else "QUALITY_REVIEW_INVALID_OUTPUT")


def parse_single_review(raw: str, finish_reason: str | None) -> SingleReviewResult:
    """拒绝截断、重复键、额外文字和过长响应，不将格式错误当作修复理由。"""
    if (finish_reason or "").upper() in {"LENGTH", "MAX_TOKENS", "CONTENT_FILTER", "CONTENT_FILTERED"}:
        raise SingleReviewOutputError(incomplete=True)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("重复 JSON 字段")
            result[key] = value
        return result

    try:
        if len(raw) > 8192 or len(raw.encode("utf-8")) > 16384:
            raise ValueError("响应超限")
        payload = json.loads(raw, object_pairs_hook=unique_object)
        result = SingleReviewResult.model_validate(payload)
        if len(result.repair_feedback) > 4000:
            raise ValueError("反馈超限")
        return result
    except (ValueError, TypeError, RecursionError):
        # Pydantic 异常含输入原文，不能沿公共失败帧或日志传播。
        raise SingleReviewOutputError() from None
