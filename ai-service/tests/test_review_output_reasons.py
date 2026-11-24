"""严格审查输出的固定失败原因，不保留供应商原文。"""

import json
import logging

import pytest

from ai_service.models.single_quality_review import (
    OUTPUT_FAILURE_REASONS, SingleReviewOutputError, SingleReviewResult, parse_single_review,
)
from test_quality_review_diagnostics import ISSUE, LOGGER_NAME, PASS, REPAIR, model_with_response


def test_failure_reason_allowlist_and_compatible_constructor():
    assert OUTPUT_FAILURE_REASONS == {
        "INCOMPLETE", "RESPONSE_TOO_LARGE", "DUPLICATE_KEY", "INVALID_JSON",
        "INVALID_SCHEMA", "VERDICT_ISSUES_MISMATCH", "FEEDBACK_TOO_LARGE", "UNCLASSIFIED",
    }
    assert SingleReviewOutputError().output_reason == "UNCLASSIFIED"
    for reason in OUTPUT_FAILURE_REASONS - {"INCOMPLETE"}:
        assert SingleReviewOutputError(reason=reason).output_reason == reason
    error = SingleReviewOutputError(incomplete=True, reason="INVALID_JSON")
    assert str(error) == "QUALITY_REVIEW_INCOMPLETE"
    assert error.output_reason == "INCOMPLETE"


@pytest.mark.parametrize("raw,reason", [
    ("", "INVALID_JSON"), ("private-json", "INVALID_JSON"),
    ('{"verdict":"PASS","issues":[],"verdict":"PASS"}', "DUPLICATE_KEY"),
    ('{"verdict":"REPAIR","issues":[{"private-key":1,"private-key":2}]}', "DUPLICATE_KEY"),
    ("[]", "INVALID_SCHEMA"), ("null", "INVALID_SCHEMA"),
    ('{"verdict":"PASS","issues":[],"private-extra":1}', "INVALID_SCHEMA"),
    ('{"verdict":true,"issues":[]}', "INVALID_SCHEMA"),
    ('{"verdict":"PASS","issues":"private-type"}', "INVALID_SCHEMA"),
    ('{"verdict":"REPAIR","issues":[]}', "VERDICT_ISSUES_MISMATCH"),
    (json.dumps({"verdict": "PASS", "issues": [ISSUE]}), "VERDICT_ISSUES_MISMATCH"),
    (json.dumps({"verdict": "REPAIR", "issues": [ISSUE] * 5}), "INVALID_SCHEMA"),
    (json.dumps({"verdict": "PASS", "issues": [{**ISSUE, "summary": ""}]}), "INVALID_SCHEMA"),
    (" " * 8193, "RESPONSE_TOO_LARGE"),
    ("中" * 5462, "RESPONSE_TOO_LARGE"),
    (json.dumps({"verdict": "REPAIR", "issues": [{**ISSUE, "summary": "x" * 160,
        "evidence": "x" * 240, "repair_hint": "x" * 240}] * 6}), "INVALID_SCHEMA"),
    (json.dumps({"verdict": "REPAIR", "issues": [{**ISSUE, "summary": "\x01" * 160,
        "evidence": "\x01" * 240, "repair_hint": "\x01" * 240}] * 2}), "FEEDBACK_TOO_LARGE"),
], ids=lambda value: str(value)[:50])
def test_fixed_output_reason_and_public_error(raw, reason):
    with pytest.raises(SingleReviewOutputError) as caught:
        parse_single_review(raw, "STOP")
    assert str(caught.value) == "QUALITY_REVIEW_INVALID_OUTPUT"
    assert caught.value.output_reason == reason
    assert "private" not in repr(caught.value.__dict__)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("finish_reason", ["length", "MAX_TOKENS", "CONTENT_FILTER", "content_filtered"])
def test_finish_reason_precedes_all_content_failures(finish_reason):
    with pytest.raises(SingleReviewOutputError) as caught:
        parse_single_review(" " * 8193, finish_reason)
    assert str(caught.value) == "QUALITY_REVIEW_INCOMPLETE"
    assert caught.value.output_reason == "INCOMPLETE"


@pytest.mark.parametrize("reason", [None, "private-reason", "NONE", "INCOMPLETE", [], {}])
def test_untrusted_constructor_reason_is_sanitized(reason):
    error = SingleReviewOutputError(reason=reason)
    assert error.output_reason == "UNCLASSIFIED"
    assert str(error) == "QUALITY_REVIEW_INVALID_OUTPUT"
    assert SingleReviewOutputError(incomplete=True, reason=reason).output_reason == "INCOMPLETE"


@pytest.mark.parametrize("raw", [PASS, REPAIR, PASS + " " * (8192 - len(PASS)),
    json.dumps({"verdict": "REPAIR", "issues": [ISSUE] * 4})], ids=lambda value: value[:50])
def test_original_valid_boundary_remains_accepted(raw):
    assert parse_single_review(raw, "STOP").verdict in {"PASS", "REPAIR"}


@pytest.mark.parametrize("raw,expected", [
    (" " * 8192, "INVALID_JSON"), (" " * 8193, "RESPONSE_TOO_LARGE"),
    ("中" * 5461 + " ", "INVALID_JSON"),
    ("中" * 5461 + "  ", "RESPONSE_TOO_LARGE"),
    ('{"private-key":1,"private-key":2}' + " " * 8192, "RESPONSE_TOO_LARGE"),
    (None, "UNCLASSIFIED"),
], ids=lambda value: str(value)[:40])
def test_response_limits_and_priority(raw, expected):
    with pytest.raises(SingleReviewOutputError) as caught:
        parse_single_review(raw, "STOP")
    assert caught.value.output_reason == expected


@pytest.mark.parametrize("size", [4000, 4001])
def test_feedback_limit_exact_boundary(size):
    # 控制字符在合法 JSON 中会膨胀；构造精确反馈长度，区别响应和反馈预算。
    payload = {"verdict": "REPAIR", "issues": [{**ISSUE, "summary": "x" * 160,
        "evidence": "x" * 240, "repair_hint": "x" * 240} for _ in range(2)]}
    for trim in range(5):
        payload["issues"][1]["repair_hint"] = "x" * (240 - trim)
        current = len(SingleReviewResult.model_validate(payload).repair_feedback)
        if (size - current) % 5 == 0:
            break
    controls = (size - current) // 5
    for issue in payload["issues"]:
        for field in ("summary", "evidence", "repair_hint"):
            count = min(controls, len(issue[field]))
            issue[field] = "\x01" * count + issue[field][count:]
            controls -= count
    assert controls == 0
    result = SingleReviewResult.model_validate(payload)
    assert len(result.repair_feedback) == size
    raw = json.dumps(payload, separators=(",", ":"))
    if size == 4000:
        assert parse_single_review(raw, "STOP") == result
    else:
        with pytest.raises(SingleReviewOutputError) as caught:
            parse_single_review(raw, "STOP")
        assert caught.value.output_reason == "FEEDBACK_TOO_LARGE"


@pytest.mark.parametrize("raw,metadata,expected", [
    (PASS, None, "NONE"), (REPAIR, None, "NONE"),
    ("private-invalid-json", None, "INVALID_JSON"),
    (PASS, {"finish_reason": "length"}, "INCOMPLETE"),
])
async def test_reason_logged_once_without_content_or_retry(caplog, raw, metadata, expected):
    model = model_with_response(raw, metadata)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        if expected in {"NONE"}:
            await model.review("private-source", {"prompt": "private-prompt"})
        else:
            with pytest.raises(SingleReviewOutputError):
                await model.review("private-source", {"prompt": "private-prompt"})
    assert len(model.messages) == 1
    assert caplog.text.count("quality_review_result") == 1
    assert f"outputReason={expected}" in caplog.text
    assert "private" not in caplog.text


@pytest.mark.parametrize("raw,expected", [(PASS, "NONE"), ("invalid", "INVALID_JSON")])
async def test_log_fault_keeps_result_and_reason(monkeypatch, raw, expected):
    def fail_log(*args, **kwargs):
        raise RuntimeError("private-log-error")

    monkeypatch.setattr(logging.getLogger(LOGGER_NAME), "info", fail_log)
    model = model_with_response(raw)
    if expected == "NONE":
        assert (await model.review("artifact", {})).passed
    else:
        with pytest.raises(SingleReviewOutputError) as caught:
            await model.review("artifact", {})
        assert caught.value.output_reason == expected
    assert len(model.messages) == 1
