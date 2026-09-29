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


def _key(issue: QualityIssue) -> tuple[str, str, str]:
    return (issue.code.casefold(), issue.category.casefold(), " ".join(issue.summary.split()).casefold())


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
        raise QualityReviewOutputError()

    deduped: dict[tuple[str, str, str], QualityIssue] = {}
    for reviewer in result_list:
        for issue in reviewer.issues:
            key = _key(issue)
            current = deduped.get(key)
            if current is None or _SEVERITY_RANK[issue.severity] > _SEVERITY_RANK[current.severity]:
                deduped[key] = issue

    if len(deduped) > 12:
        raise QualityReviewOutputError()
    ordered = list(deduped.values())
    blocking = [issue for issue in ordered if issue.severity in (IssueSeverity.CRITICAL, IssueSeverity.MAJOR)]
    minor = [issue for issue in ordered if issue.severity is IssueSeverity.MINOR]
    feedback = _feedback(blocking)
    if len(feedback) > 4000:
        raise QualityReviewOutputError()
    return QualityReviewResult(
        passed=not blocking,
        reviewer_results=result_list,
        blocking_issues=blocking,
        minor_issues=minor,
        repair_feedback=feedback,
    )
