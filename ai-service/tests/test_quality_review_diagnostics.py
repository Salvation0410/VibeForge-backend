"""单 Reviewer 严格协议、固定日志、调用预算和取消边界。"""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from ai_service.models.openai_compatible import OpenAICompatibleModel
from ai_service.models.single_quality_review import SingleReviewOutputError, parse_single_review


LOGGER_NAME = "ai_service.models.openai_compatible"
PASS = '{"verdict":"PASS","issues":[]}'
ISSUE = {"code": "SCOPE_VIOLATION", "summary": "private-summary",
         "evidence": "private-evidence", "repair_hint": "private-hint"}
REPAIR = json.dumps({"verdict": "REPAIR", "issues": [ISSUE]})


def model_with_response(content, metadata=None):
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model.messages = []

    async def invoke(messages):
        model.messages.append(messages)
        return SimpleNamespace(content=content, response_metadata=metadata)

    model._invoke = invoke
    return model


@pytest.mark.parametrize("content,passed", [(PASS, True), (REPAIR, False)])
async def test_structured_diagnostic_redacts_content_and_passes_feedback(caplog, content, passed):
    model = model_with_response(content, {"finish_reason": "stop", "private": "private-metadata"})
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        result = await model.review("private-source", {"prompt": "private-prompt", "artifact": "stale"})
    assert result.passed is passed
    assert len(model.messages) == 1
    assert json.loads(model.messages[0][1].content)["artifact"] == "private-source"
    if passed:
        assert result.repair_feedback == ""
    else:
        assert json.loads(result.repair_feedback)["issues"][0] == ISSUE
    assert f"verdict={result.verdict}" in caplog.text
    assert f"issueCount={len(result.issues)}" in caplog.text
    assert "private-" not in caplog.text


@pytest.mark.parametrize("raw", [
    "PASS", "REPAIR", "", "PASS.", "```json\n" + PASS + "\n```", "[]", "null",
    '{"verdict":"PASS","issues":[],"extra":"private"}',
    '{"verdict":"PASS","verdict":"REPAIR","issues":[]}',
    '{"verdict":"REPAIR","issues":[]}',
    json.dumps({"verdict": "PASS", "issues": [ISSUE]}),
    json.dumps({"verdict": "REPAIR", "issues": [ISSUE] * 5}),
    json.dumps({"verdict": True, "issues": []}),
    pytest.param(" " * 8193, id="too-many-chars"),
    pytest.param("中" * 6000, id="too-many-utf8-bytes"),
])
async def test_invalid_response_fails_closed_without_retry_or_raw_log(caplog, raw):
    model = model_with_response(raw)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME), pytest.raises(SingleReviewOutputError) as error:
        await model.review("private-source", {})
    assert str(error.value) == "QUALITY_REVIEW_INVALID_OUTPUT"
    assert len(model.messages) == 1
    assert "verdict=INVALID_OUTPUT" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize("field,value", [
    ("code", "private-code"), ("summary", " "), ("summary", "x" * 161),
    ("evidence", "x" * 241), ("repair_hint", "x" * 241),
    ("evidence", 1), ("extra", "private-field"),
])
def test_issue_schema_limits(field, value):
    with pytest.raises(SingleReviewOutputError):
        parse_single_review(json.dumps({"verdict": "REPAIR", "issues": [{**ISSUE, field: value}]}), "STOP")


@pytest.mark.parametrize("reason", ["length", "max_tokens", "content_filter", "CONTENT_FILTERED"])
@pytest.mark.parametrize("content", [PASS, REPAIR])
async def test_incomplete_even_valid_json_is_never_accepted(reason, content):
    with pytest.raises(SingleReviewOutputError, match="QUALITY_REVIEW_INCOMPLETE"):
        await model_with_response(content, {"finish_reason": reason}).review("artifact", {})


@pytest.mark.parametrize("metadata,expected", [
    ({"stop_reason": "stop"}, "STOP"), ({"finish_reason": "private-reason"}, "UNKNOWN"),
    ({"finish_reason": {"private": "metadata"}}, "UNKNOWN"),
    (None, "UNKNOWN"), ("private-metadata", "UNKNOWN"),
])
async def test_finish_reason_allowlist(caplog, metadata, expected):
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        assert (await model_with_response(PASS, metadata).review("artifact", {})).passed
    assert f"finishReason={expected}" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize("content", [PASS, REPAIR, "invalid"])
async def test_logging_failure_does_not_override_result(monkeypatch, content):
    def broken_log(*args, **kwargs):
        raise RuntimeError("private-logging-error")

    monkeypatch.setattr(logging.getLogger(LOGGER_NAME), "info", broken_log)
    if content == "invalid":
        with pytest.raises(SingleReviewOutputError):
            await model_with_response(content).review("artifact", {})
    else:
        assert (await model_with_response(content).review("artifact", {})).passed is (content == PASS)


@pytest.mark.parametrize("failure", [RuntimeError("private-provider-error"), asyncio.CancelledError()])
async def test_provider_failure_and_cancel_propagate(caplog, failure):
    model = model_with_response(PASS)

    async def fail(messages):
        raise failure

    model._invoke = fail
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME), pytest.raises(type(failure)) as caught:
        await model.review("private-source", {"prompt": "private-prompt"})
    assert caught.value is failure
    assert "quality_review_result" not in caplog.text
