"""单 Reviewer 的有界诊断契约；模型文字不是系统指令或确定性验收证据。"""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from pydantic_core import PydanticCustomError


OUTPUT_FAILURE_REASONS = frozenset({
    "INCOMPLETE", "RESPONSE_TOO_LARGE", "DUPLICATE_KEY", "INVALID_JSON",
    "INVALID_SCHEMA", "VERDICT_ISSUES_MISMATCH", "FEEDBACK_TOO_LARGE", "UNCLASSIFIED",
})
_VERDICT_MISMATCH_TYPE = "single_review_verdict_issues_mismatch"


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
            raise PydanticCustomError(_VERDICT_MISMATCH_TYPE, "判定与问题列表不一致")
        return self

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"

    @property
    def repair_feedback(self) -> str:
        # JSON 保留字段边界；反馈始终处于用户载荷，不拼入系统提示。
        return "" if self.passed else self.model_dump_json()


class SingleReviewOutputError(ValueError):
    def __init__(self, *, incomplete: bool = False, reason: str | None = None):
        super().__init__("QUALITY_REVIEW_INCOMPLETE" if incomplete else "QUALITY_REVIEW_INVALID_OUTPUT")
        # 结束原因优先；不能让不可信原因伪造与公共错误码矛盾的诊断。
        self.output_reason = "INCOMPLETE" if incomplete else (
            reason if isinstance(reason, str) and reason in OUTPUT_FAILURE_REASONS
            and reason != "INCOMPLETE" else "UNCLASSIFIED"
        )


class _DuplicateReviewKey(ValueError):
    """仅用于解析控制流，不携带原始 JSON 键或值。"""


def parse_single_review(raw: str, finish_reason: str | None) -> SingleReviewResult:
    """拒绝截断、重复键、额外文字和过长响应，不将格式错误当作修复理由。"""
    if (finish_reason or "").upper() in {"LENGTH", "MAX_TOKENS", "CONTENT_FILTER", "CONTENT_FILTERED"}:
        raise SingleReviewOutputError(incomplete=True)

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateReviewKey()
            result[key] = value
        return result

    try:
        if len(raw) > 8192 or len(raw.encode("utf-8")) > 16384:
            raise SingleReviewOutputError(reason="RESPONSE_TOO_LARGE")
        payload = json.loads(raw, object_pairs_hook=unique_object)
    except SingleReviewOutputError:
        raise
    except _DuplicateReviewKey:
        raise SingleReviewOutputError(reason="DUPLICATE_KEY") from None
    except json.JSONDecodeError:
        raise SingleReviewOutputError(reason="INVALID_JSON") from None
    except (ValueError, TypeError, RecursionError):
        raise SingleReviewOutputError() from None

    try:
        result = SingleReviewResult.model_validate(payload)
    except ValidationError as exc:
        # 只读取自有验证器的固定类型；不导出正文、输入、位置或字段名。
        errors = exc.errors(include_url=False, include_context=False, include_input=False)
        reason = "VERDICT_ISSUES_MISMATCH" if errors and all(
            item["type"] == _VERDICT_MISMATCH_TYPE for item in errors
        ) else "INVALID_SCHEMA"
        raise SingleReviewOutputError(reason=reason) from None
    except (ValueError, TypeError, RecursionError):
        raise SingleReviewOutputError() from None
    try:
        feedback_size = len(result.repair_feedback)
    except (ValueError, TypeError, RecursionError):
        raise SingleReviewOutputError() from None
    if feedback_size > 4000:
        raise SingleReviewOutputError(reason="FEEDBACK_TOO_LARGE")
    return result
