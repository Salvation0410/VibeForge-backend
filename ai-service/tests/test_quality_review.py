import pytest
from pydantic import ValidationError

from ai_service.models.quality_review import (
    IssueSeverity,
    QualityIssue,
    QualityReviewOutputError,
    QualityReviewResult,
    ReviewerResult,
    ReviewerRole,
)
from ai_service.orchestration.multi_agent_review import aggregate_review_results


def issue(role: ReviewerRole, severity: IssueSeverity, code: str = "ISSUE", category: str = "logic", summary: str = "Problem") -> ReviewerResult:
    return ReviewerResult(
        reviewer=role,
        summary=f"{role.value} summary",
        issues=[QualityIssue(code=code, category=category, summary=summary, evidence="evidence", repair_hint="repair", severity=severity)],
    )


def trio(*results: ReviewerResult) -> list[ReviewerResult]:
    return list(results)


def empty_reviewer_results() -> list[ReviewerResult]:
    return [
        ReviewerResult(reviewer=ReviewerRole.REQUIREMENT, summary="ok"),
        ReviewerResult(reviewer=ReviewerRole.FUNCTION, summary="ok"),
        ReviewerResult(reviewer=ReviewerRole.TECHNICAL, summary="ok"),
    ]


def test_quality_review_result_rejects_non_strict_passed_value():
    with pytest.raises(ValidationError):
        QualityReviewResult(
            passed=1,
            reviewer_results=empty_reviewer_results(),
            blocking_issues=[],
            minor_issues=[],
            repair_feedback="",
        )


def test_quality_review_result_strips_repair_feedback():
    result = QualityReviewResult(
        passed=True,
        reviewer_results=empty_reviewer_results(),
        blocking_issues=[],
        minor_issues=[],
        repair_feedback=" x ",
    )

    assert result.repair_feedback == "x"


def test_three_reviewers_without_blockers_pass():
    result = aggregate_review_results(trio(
        ReviewerResult(reviewer=ReviewerRole.REQUIREMENT, summary="ok"),
        ReviewerResult(reviewer=ReviewerRole.FUNCTION, summary="ok"),
        ReviewerResult(reviewer=ReviewerRole.TECHNICAL, summary="ok"),
    ))
    assert result.passed is True
    assert result.blocking_issues == []
    assert result.repair_feedback == ""


def test_minor_does_not_block_and_feedback_is_empty():
    result = aggregate_review_results(trio(
        issue(ReviewerRole.REQUIREMENT, IssueSeverity.MINOR),
        ReviewerResult(reviewer=ReviewerRole.FUNCTION, summary="ok"),
        ReviewerResult(reviewer=ReviewerRole.TECHNICAL, summary="ok"),
    ))
    assert result.passed is True
    assert len(result.minor_issues) == 1
    assert result.repair_feedback == ""


def test_critical_and_major_block_with_feedback():
    result = aggregate_review_results(trio(
        issue(ReviewerRole.REQUIREMENT, IssueSeverity.CRITICAL, code="C"),
        issue(ReviewerRole.FUNCTION, IssueSeverity.MAJOR, code="M"),
        ReviewerResult(reviewer=ReviewerRole.TECHNICAL, summary="ok"),
    ))
    assert result.passed is False
    assert [x.severity for x in result.blocking_issues] == [IssueSeverity.CRITICAL, IssueSeverity.MAJOR]
    assert "1." in result.repair_feedback and "severity=critical" in result.repair_feedback


def test_duplicate_issue_keeps_highest_severity():
    result = aggregate_review_results(trio(
        issue(ReviewerRole.REQUIREMENT, IssueSeverity.MINOR, summary="same"),
        issue(ReviewerRole.FUNCTION, IssueSeverity.CRITICAL, summary=" same  "),
        issue(ReviewerRole.TECHNICAL, IssueSeverity.MAJOR, summary="same"),
    ))
    assert len(result.blocking_issues) == 1
    assert result.blocking_issues[0].severity is IssueSeverity.CRITICAL


@pytest.mark.parametrize("roles", [
    [ReviewerRole.REQUIREMENT, ReviewerRole.FUNCTION],
    [ReviewerRole.REQUIREMENT, ReviewerRole.FUNCTION, ReviewerRole.FUNCTION],
])
def test_missing_or_duplicate_role_fails(roles):
    with pytest.raises(QualityReviewOutputError, match="MULTI_AGENT_REVIEW_INVALID_OUTPUT"):
        aggregate_review_results([ReviewerResult(reviewer=role, summary="ok") for role in roles])


def test_single_reviewer_more_than_five_issues_fails():
    with pytest.raises(ValidationError):
        ReviewerResult(reviewer=ReviewerRole.REQUIREMENT, summary="x", issues=[
            QualityIssue(code=str(i), category="c", summary="s", evidence="e", repair_hint="r", severity=IssueSeverity.MINOR)
            for i in range(6)
        ])


@pytest.mark.parametrize("field", ["code", "category", "summary", "evidence", "repair_hint"])
def test_quality_issue_field_length_boundaries_fail(field):
    values = dict(code="c", category="c", summary="s", evidence="e", repair_hint="r", severity=IssueSeverity.MINOR)
    limits = {"code": 64, "category": 64, "summary": 300, "evidence": 600, "repair_hint": 600}
    values[field] = "x" * (limits[field] + 1)
    with pytest.raises(ValidationError):
        QualityIssue(**values)


def test_more_than_twelve_unique_issues_fails():
    results = [
        ReviewerResult(
            reviewer=role,
            summary="ok",
            issues=[
                QualityIssue(
                    code=f"{role.value}-{index}",
                    category="c",
                    summary="s",
                    evidence="e",
                    repair_hint="r",
                    severity=IssueSeverity.MAJOR,
                )
                for index in range(issue_count)
            ],
        )
        for role, issue_count in (
            (ReviewerRole.REQUIREMENT, 5),
            (ReviewerRole.FUNCTION, 4),
            (ReviewerRole.TECHNICAL, 4),
        )
    ]
    with pytest.raises(QualityReviewOutputError) as error:
        aggregate_review_results(results)
    assert str(error.value) == "MULTI_AGENT_REVIEW_INVALID_OUTPUT"


def test_feedback_over_four_thousand_characters_fails():
    results = [
        ReviewerResult(reviewer=ReviewerRole.REQUIREMENT, summary="ok", issues=[QualityIssue(code=str(i), category="c", summary="s", evidence="e" * 600, repair_hint="r" * 600, severity=IssueSeverity.MAJOR) for i in range(4)]),
        ReviewerResult(reviewer=ReviewerRole.FUNCTION, summary="ok"),
        ReviewerResult(reviewer=ReviewerRole.TECHNICAL, summary="ok"),
    ]
    with pytest.raises(QualityReviewOutputError) as error:
        aggregate_review_results(results)
    assert str(error.value) == "MULTI_AGENT_REVIEW_INVALID_OUTPUT"
