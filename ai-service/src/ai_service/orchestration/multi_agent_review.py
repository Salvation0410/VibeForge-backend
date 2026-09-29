from collections.abc import Sequence

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
