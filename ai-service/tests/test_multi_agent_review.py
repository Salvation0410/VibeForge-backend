import asyncio
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from ai_service.models.quality_review import (
    QualityReviewOutputError,
    ReviewerResult,
    ReviewerRole,
)
from ai_service.orchestration import multi_agent_review
from ai_service.orchestration.multi_agent_review import (
    MultiAgentReviewError,
    run_multi_agent_review,
)


ReviewBehavior = Callable[
    [ReviewerRole, str, dict[str, Any]],
    Awaitable[ReviewerResult],
]


class FakeReviewModel:
    def __init__(self, behavior: ReviewBehavior) -> None:
        self._behavior = behavior
        self.reviewer_tasks: dict[ReviewerRole, asyncio.Task[Any]] = {}

    async def review_role(
        self,
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        current_task = asyncio.current_task()
        assert current_task is not None
        self.reviewer_tasks[role] = current_task
        return await self._behavior(role, artifact, context)


def result(role: ReviewerRole) -> ReviewerResult:
    return ReviewerResult(reviewer=role, summary=f"{role.value} ok")


def assert_all_reviewer_tasks_done(model: FakeReviewModel) -> None:
    assert set(model.reviewer_tasks) == set(ReviewerRole)
    reviewer_tasks = set(model.reviewer_tasks.values())
    assert all(task.done() for task in reviewer_tasks)
    assert reviewer_tasks.isdisjoint(asyncio.all_tasks())


@pytest.mark.asyncio
async def test_all_reviewers_start_before_any_can_finish() -> None:
    started: set[ReviewerRole] = set()
    all_started = asyncio.Event()

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        started.add(role)
        if started == set(ReviewerRole):
            all_started.set()
        await all_started.wait()
        return result(role)

    review_result = await run_multi_agent_review(
        FakeReviewModel(review),
        "artifact",
        {},
        timeout_seconds=1,
    )

    assert started == set(ReviewerRole)
    assert review_result.passed is True


@pytest.mark.asyncio
async def test_successful_results_are_aggregated_in_enum_order() -> None:
    delays = {
        ReviewerRole.REQUIREMENT: 0.03,
        ReviewerRole.FUNCTION: 0.02,
        ReviewerRole.TECHNICAL: 0.01,
    }

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        await asyncio.sleep(delays[role])
        return result(role)

    review_result = await run_multi_agent_review(
        FakeReviewModel(review),
        "artifact",
        {},
        timeout_seconds=1,
    )

    assert [item.reviewer for item in review_result.reviewer_results] == list(ReviewerRole)


@pytest.mark.asyncio
async def test_shared_timeout_cancels_and_cleans_up_all_reviewers() -> None:
    started: set[ReviewerRole] = set()
    cancelled: set[ReviewerRole] = set()

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        started.add(role)
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.add(role)
            raise

    model = FakeReviewModel(review)
    with pytest.raises(MultiAgentReviewError) as caught:
        await run_multi_agent_review(
            model,
            "artifact",
            {},
            timeout_seconds=0.02,
        )

    assert str(caught.value) == (
        "MULTI_AGENT_REVIEW_TIMEOUT: quality reviewers exceeded the configured timeout"
    )
    assert started == set(ReviewerRole)
    assert cancelled == set(ReviewerRole)
    assert_all_reviewer_tasks_done(model)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_invalid_output_cancels_siblings_and_drops_raw_exception_chain() -> None:
    all_started = asyncio.Event()
    started: set[ReviewerRole] = set()
    cancelled: set[ReviewerRole] = set()

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        started.add(role)
        if started == set(ReviewerRole):
            all_started.set()
        await all_started.wait()
        if role is ReviewerRole.REQUIREMENT:
            raise QualityReviewOutputError("invalid_json")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.add(role)
            raise

    model = FakeReviewModel(review)
    with pytest.raises(MultiAgentReviewError) as caught:
        await run_multi_agent_review(
            model,
            "artifact",
            {},
            timeout_seconds=1,
        )

    assert str(caught.value) == "MULTI_AGENT_REVIEW_INVALID_OUTPUT: invalid JSON"
    assert cancelled == {ReviewerRole.FUNCTION, ReviewerRole.TECHNICAL}
    assert_all_reviewer_tasks_done(model)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.asyncio
async def test_nested_base_exception_group_with_cancelled_error_maps_invalid_output() -> None:
    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        if role is ReviewerRole.REQUIREMENT:
            raise BaseExceptionGroup(
                "outer",
                [
                    BaseExceptionGroup(
                        "inner",
                        [
                            QualityReviewOutputError("identity_mismatch"),
                            asyncio.CancelledError(),
                        ],
                    )
                ],
            )
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    model = FakeReviewModel(review)
    with pytest.raises(MultiAgentReviewError) as caught:
        await run_multi_agent_review(
            model,
            "artifact",
            {},
            timeout_seconds=1,
        )

    assert str(caught.value) == (
        "MULTI_AGENT_REVIEW_INVALID_OUTPUT: reviewer identity mismatch"
    )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert_all_reviewer_tasks_done(model)


@pytest.mark.asyncio
async def test_external_cancellation_wins_over_cleanup_output_error_group() -> None:
    all_started = asyncio.Event()
    started: set[ReviewerRole] = set()

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        started.add(role)
        if started == set(ReviewerRole):
            all_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if role is ReviewerRole.REQUIREMENT:
                raise BaseExceptionGroup(
                    "cleanup",
                    [
                        asyncio.CancelledError(),
                        QualityReviewOutputError("identity_mismatch"),
                    ],
                )
            raise

    model = FakeReviewModel(review)
    parent = asyncio.create_task(
        run_multi_agent_review(
            model,
            "artifact",
            {},
            timeout_seconds=10,
        )
    )
    await asyncio.wait_for(all_started.wait(), timeout=1)
    parent.cancel()

    with pytest.raises(asyncio.CancelledError):
        await parent

    assert not isinstance(parent.exception() if not parent.cancelled() else None, MultiAgentReviewError)
    assert parent.cancelled()
    assert_all_reviewer_tasks_done(model)


@pytest.mark.asyncio
async def test_mixed_fatal_group_propagates_only_system_exit_without_secret() -> None:
    secret = "fatal-group-secret-6bc8"

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        if role is ReviewerRole.REQUIREMENT:
            raise BaseExceptionGroup(
                "fatal",
                [SystemExit(17), RuntimeError(secret)],
            )
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    model = FakeReviewModel(review)
    with pytest.raises(SystemExit) as caught:
        await run_multi_agent_review(
            model,
            "artifact",
            {},
            timeout_seconds=1,
        )

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert caught.value.code == 17
    assert secret not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert_all_reviewer_tasks_done(model)


@pytest.mark.asyncio
async def test_multiple_fatal_leaves_use_fixed_redacted_group() -> None:
    secret = "multiple-fatal-secret-53ad"

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        if role is ReviewerRole.REQUIREMENT:
            raise BaseExceptionGroup(
                "provider group containing secret",
                [SystemExit(11), RuntimeError(secret), SystemExit(12)],
            )
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    model = FakeReviewModel(review)
    with pytest.raises(BaseExceptionGroup) as caught:
        await run_multi_agent_review(
            model,
            "artifact",
            {},
            timeout_seconds=1,
        )

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert caught.value.message == (
        "MULTI_AGENT_REVIEW_FATAL: quality reviewers raised fatal exceptions"
    )
    assert [error.code for error in caught.value.exceptions] == [11, 12]
    assert secret not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert_all_reviewer_tasks_done(model)


@pytest.mark.asyncio
async def test_runtime_error_is_redacted_from_error_and_traceback() -> None:
    secret = "unique-provider-secret-91f7"
    all_started = asyncio.Event()
    started: set[ReviewerRole] = set()

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        started.add(role)
        if started == set(ReviewerRole):
            all_started.set()
        await all_started.wait()
        if role is ReviewerRole.FUNCTION:
            raise RuntimeError(secret)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    model = FakeReviewModel(review)
    with pytest.raises(MultiAgentReviewError) as caught:
        await run_multi_agent_review(
            model,
            "artifact",
            {},
            timeout_seconds=1,
        )

    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert str(caught.value) == (
        "MULTI_AGENT_REVIEW_MODEL_ERROR: quality reviewer call failed"
    )
    assert secret not in str(caught.value)
    assert secret not in rendered
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert_all_reviewer_tasks_done(model)


@pytest.mark.asyncio
async def test_parent_cancellation_propagates_and_cancels_all_reviewers() -> None:
    all_started = asyncio.Event()
    started: set[ReviewerRole] = set()
    cancelled: set[ReviewerRole] = set()

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        started.add(role)
        if started == set(ReviewerRole):
            all_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.add(role)
            raise

    model = FakeReviewModel(review)
    parent = asyncio.create_task(
        run_multi_agent_review(
            model,
            "artifact",
            {},
            timeout_seconds=10,
        )
    )
    await asyncio.wait_for(all_started.wait(), timeout=1)
    parent.cancel()

    with pytest.raises(asyncio.CancelledError):
        await parent

    assert cancelled == set(ReviewerRole)
    assert_all_reviewer_tasks_done(model)


@pytest.mark.asyncio
async def test_each_reviewer_receives_an_independent_shallow_context_copy() -> None:
    caller_context: dict[str, Any] = {"shared": ["same-object"], "value": "original"}
    context_ids: dict[ReviewerRole, int] = {}
    shared_ids: dict[ReviewerRole, int] = {}
    observed_values: dict[ReviewerRole, str] = {}

    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        context_ids[role] = id(context)
        shared_ids[role] = id(context["shared"])
        observed_values[role] = context["value"]
        if role is ReviewerRole.REQUIREMENT:
            context["value"] = "mutated"
            context["reviewer_only"] = True
        await asyncio.sleep(0)
        return result(role)

    await run_multi_agent_review(
        FakeReviewModel(review),
        "artifact",
        caller_context,
        timeout_seconds=1,
    )

    assert len(set(context_ids.values())) == 3
    assert set(shared_ids.values()) == {id(caller_context["shared"])}
    assert observed_values == {role: "original" for role in ReviewerRole}
    assert caller_context == {"shared": ["same-object"], "value": "original"}


@pytest.mark.asyncio
async def test_aggregate_output_error_is_mapped_without_a_cause(monkeypatch: pytest.MonkeyPatch) -> None:
    async def review(
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        return result(role)

    def fail_aggregate(results: list[ReviewerResult]) -> None:
        raise QualityReviewOutputError("too_many_issues")

    monkeypatch.setattr(multi_agent_review, "aggregate_review_results", fail_aggregate)

    with pytest.raises(MultiAgentReviewError) as caught:
        await run_multi_agent_review(
            FakeReviewModel(review),
            "artifact",
            {},
            timeout_seconds=1,
        )

    assert str(caught.value) == (
        "MULTI_AGENT_REVIEW_INVALID_OUTPUT: aggregated issues exceed 12"
    )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
