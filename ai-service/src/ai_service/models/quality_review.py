from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class ReviewerRole(StrEnum):
    REQUIREMENT = "requirement"
    FUNCTION = "function"
    TECHNICAL = "technical"


class IssueSeverity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


_OUTPUT_ERROR_MESSAGES = {
    "invalid_output": "MULTI_AGENT_REVIEW_INVALID_OUTPUT",
    "invalid_json": "MULTI_AGENT_REVIEW_INVALID_OUTPUT: invalid JSON",
    "identity_mismatch": "MULTI_AGENT_REVIEW_INVALID_OUTPUT: reviewer identity mismatch",
    "too_many_issues": "MULTI_AGENT_REVIEW_INVALID_OUTPUT: aggregated issues exceed 12",
    "feedback_too_long": "MULTI_AGENT_REVIEW_INVALID_OUTPUT: repair feedback exceeds 4000 characters",
}


class QualityReviewOutputError(ValueError):
    def __init__(self, reason: str = "invalid_output") -> None:
        super().__init__(_OUTPUT_ERROR_MESSAGES.get(reason, _OUTPUT_ERROR_MESSAGES["invalid_output"]))


Trimmed = Annotated[str, StringConstraints(strip_whitespace=True)]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class QualityIssue(_StrictModel):
    code: Annotated[Trimmed, Field(min_length=1, max_length=64)]
    category: Annotated[Trimmed, Field(min_length=1, max_length=64)]
    summary: Annotated[Trimmed, Field(min_length=1, max_length=300)]
    evidence: Annotated[Trimmed, Field(min_length=1, max_length=600)]
    repair_hint: Annotated[Trimmed, Field(min_length=1, max_length=600)]
    severity: IssueSeverity


class ReviewerResult(_StrictModel):
    reviewer: ReviewerRole
    summary: Annotated[Trimmed, Field(min_length=1, max_length=300)]
    issues: Annotated[list[QualityIssue], Field(default_factory=list, max_length=5)]


class QualityReviewResult(_StrictModel):
    passed: bool
    reviewer_results: Annotated[list[ReviewerResult], Field(min_length=3, max_length=3)]
    blocking_issues: Annotated[list[QualityIssue], Field(max_length=12)]
    minor_issues: Annotated[list[QualityIssue], Field(max_length=12)]
    repair_feedback: Annotated[Trimmed, Field(max_length=4000)]
