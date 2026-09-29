import asyncio
from collections.abc import Sequence
from typing import Any

from ai_service.models.base import GenerationModel
from ai_service.models.quality_review import (
    IssueSeverity,
    QualityIssue,
    QualityReviewOutputError,
    QualityReviewResult,
    ReviewerResult,
    ReviewerRole,
)


_SEVERITY_RANK = {
    IssueSeverity.MINOR: 0,
    IssueSeverity.MAJOR: 1,
    IssueSeverity.CRITICAL: 2,
}
_REQUIRED_ROLES = frozenset(ReviewerRole)
_ROLE_RANK = {role: index for index, role in enumerate(ReviewerRole)}
_TIMEOUT_MESSAGE = (
    "MULTI_AGENT_REVIEW_TIMEOUT: quality reviewers exceeded the configured timeout"
)
_MODEL_ERROR_MESSAGE = "MULTI_AGENT_REVIEW_MODEL_ERROR: quality reviewer call failed"


class MultiAgentReviewError(RuntimeError):
    """多角色质量审查协调失败后的稳定、脱敏外层异常。"""


def _find_output_error(error: Exception) -> QualityReviewOutputError | None:
    if isinstance(error, QualityReviewOutputError):
        return error
    if isinstance(error, ExceptionGroup):
        for nested in error.exceptions:
            output_error = _find_output_error(nested)
            if output_error is not None:
                return output_error
    return None


def _key(issue: QualityIssue) -> tuple[str, str, str]:
    return (issue.code.casefold(), issue.category.casefold(), " ".join(issue.summary.split()).casefold())


def _choice_key(issue: QualityIssue) -> tuple[str, ...]:
    return (
        issue.evidence.casefold(),
        issue.evidence,
        issue.repair_hint.casefold(),
        issue.repair_hint,
        issue.code.casefold(),
        issue.code,
        issue.category.casefold(),
        issue.category,
        issue.summary.casefold(),
        issue.summary,
    )


def _issue_sort_key(issue: QualityIssue) -> tuple[tuple[str, str, str], int, tuple[str, ...]]:
    return (_key(issue), -_SEVERITY_RANK[issue.severity], _choice_key(issue))


def _feedback(issues: list[QualityIssue]) -> str:
    if not issues:
        return ""
    lines: list[str] = []
    for index, issue in enumerate(issues, 1):
        lines.append(
            f"{index}. severity={issue.severity.value}; code={issue.code}; issue={issue.summary}; "
            f"evidence={issue.evidence}; repair={issue.repair_hint}"
        )
    return "\n".join(lines)


def aggregate_review_results(results: Sequence[ReviewerResult]) -> QualityReviewResult:
    result_list = list(results)
    if len(result_list) != 3 or {result.reviewer for result in result_list} != _REQUIRED_ROLES:
        raise QualityReviewOutputError("identity_mismatch")

    ordered_results = [
        result.model_copy(update={"issues": sorted(result.issues, key=_issue_sort_key)})
        for result in sorted(result_list, key=lambda result: _ROLE_RANK[result.reviewer])
    ]

    deduped: dict[tuple[str, str, str], QualityIssue] = {}
    for reviewer in ordered_results:
        for issue in reviewer.issues:
            key = _key(issue)
            current = deduped.get(key)
            if (
                current is None
                or _SEVERITY_RANK[issue.severity] > _SEVERITY_RANK[current.severity]
                or (
                    issue.severity is current.severity
                    and _choice_key(issue) < _choice_key(current)
                )
            ):
                deduped[key] = issue

    if len(deduped) > 12:
        raise QualityReviewOutputError("too_many_issues")
    ordered = sorted(deduped.values(), key=_issue_sort_key)
    blocking = [issue for issue in ordered if issue.severity in (IssueSeverity.CRITICAL, IssueSeverity.MAJOR)]
    minor = [issue for issue in ordered if issue.severity is IssueSeverity.MINOR]
    feedback = _feedback(blocking)
    if len(feedback) > 4000:
        raise QualityReviewOutputError("feedback_too_long")
    return QualityReviewResult(
        passed=not blocking,
        reviewer_results=ordered_results,
        blocking_issues=blocking,
        minor_issues=minor,
        repair_feedback=feedback,
    )


async def run_multi_agent_review(
    model: GenerationModel,
    artifact: str,
    context: dict[str, Any],
    *,
    timeout_seconds: float,
) -> QualityReviewResult:
    tasks: dict[ReviewerRole, asyncio.Task[ReviewerResult]] = {}
    mapped_error: str | None = None

    try:
        async with asyncio.timeout(timeout_seconds):
            async with asyncio.TaskGroup() as task_group:
                for role in ReviewerRole:
                    tasks[role] = task_group.create_task(
                        model.review_role(role, artifact, dict(context))
                    )
    except TimeoutError:
        mapped_error = _TIMEOUT_MESSAGE
    except ExceptionGroup as error_group:
        output_error = _find_output_error(error_group)
        mapped_error = str(output_error) if output_error is not None else _MODEL_ERROR_MESSAGE
    except Exception:
        mapped_error = _MODEL_ERROR_MESSAGE

    if mapped_error is not None:
        raise MultiAgentReviewError(mapped_error) from None

    ordered_results = [tasks[role].result() for role in ReviewerRole]
    aggregate_error: str | None = None
    try:
        return aggregate_review_results(ordered_results)
    except QualityReviewOutputError as error:
        aggregate_error = str(error)
    except Exception:
        aggregate_error = _MODEL_ERROR_MESSAGE

    raise MultiAgentReviewError(aggregate_error) from None
