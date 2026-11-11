"""单 Reviewer 的固定诊断不泄漏正文，也不改变判定与异常语义。"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from ai_service.models.openai_compatible import OpenAICompatibleModel


LOGGER_NAME = "ai_service.models.openai_compatible"


def model_with_response(content, metadata=None):
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)

    async def invoke(messages):
        return SimpleNamespace(content=content, response_metadata=metadata)

    model._invoke = invoke
    return model


@pytest.mark.parametrize("content, verdict, passed", [
    (" PASS\n", "PASS", True), ("repair", "REPAIR", False),
    ("private-model-output", "INVALID_OUTPUT", False),
    ("", "INVALID_OUTPUT", False), ("PASS.", "INVALID_OUTPUT", False),
])
async def test_fixed_diagnostic_preserves_return_value_and_redacts_content(caplog, content, verdict, passed):
    model = model_with_response(content, {"finish_reason": "stop", "private": "private-metadata"})
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert await model.review("private-source", {"prompt": "private-prompt"}) is passed
    records = [record for record in caplog.records if record.name == LOGGER_NAME]
    assert len(records) == 1
    assert records[0].getMessage() == (
        f"quality_review_result verdict={verdict} responseChars={len(content)} finishReason=STOP"
    )
    assert records[0].exc_info is None
    assert "private-" not in caplog.text


@pytest.mark.parametrize("metadata, expected", [
    ({"finish_reason": "length"}, "LENGTH"),
    ({"finish_reason": "content_filter"}, "CONTENT_FILTER"),
    ({"stop_reason": "stop"}, "STOP"),
    ({"finish_reason": "private-finish-reason"}, "UNKNOWN"),
    ({"finish_reason": {"private": "metadata"}}, "UNKNOWN"),
    (None, "UNKNOWN"), ("private-metadata", "UNKNOWN"),
])
async def test_finish_reason_is_projected_to_fixed_allowlist(caplog, metadata, expected):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert await model_with_response("PASS", metadata).review("artifact", {}) is True
    assert f"finishReason={expected}" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize("content, passed", [("PASS", True), ("REPAIR", False), ("invalid", False)])
async def test_logging_failure_does_not_change_boolean(monkeypatch, content, passed):
    def broken_log(*args, **kwargs):
        raise RuntimeError("private-logging-error")

    monkeypatch.setattr(logging.getLogger(LOGGER_NAME), "info", broken_log)
    assert await model_with_response(content).review("artifact", {}) is passed


@pytest.mark.parametrize("failure", [RuntimeError("private-provider-error"), asyncio.CancelledError()])
async def test_provider_failure_and_cancellation_propagate_without_result_log(caplog, failure):
    model = model_with_response("PASS")

    async def fail(messages):
        raise failure

    model._invoke = fail
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME), pytest.raises(type(failure)) as caught:
        await model.review("private-source", {"prompt": "private-prompt"})
    assert caught.value is failure
    assert "quality_review_result" not in caplog.text
    assert "private" not in caplog.text
