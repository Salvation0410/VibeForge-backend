"""回放证据、预算和故障语义使用离线假HTTP验证，不访问真实模型。"""
from dataclasses import asdict
import json

import httpx
import pytest

from ai_service.evaluation.citation_replay import (
    Context, Unreplayable, digest, execute, normalize_usage, restore_contexts,
    schedule, strict_v1, summarize, validate_answer,
)


def context():
    return Context("doc:1:0", "正文", "doc", 1, digest("正文"), digest("正文"))


def case(*, safety=False):
    return {"id": "safe" if safety else "case", "question": "问题",
            "expectedAnswerable": not safety, "expectedDocumentIds": [] if safety else ["logical"],
            "expectedActualDocumentIds": [] if safety else ["doc"],
            "category": "safety" if safety else "product", "questionType": "direct",
            "referenceAnnotation": {"expectedFacts": ["绝不泄漏的事实"], "forbiddenClaims": ["标注"]},
            "contexts": [asdict(context())],
            "userPrompt": '{"question":"问题","contexts":[{"chunkId":"doc:1:0","content":"正文"}]}',
            "userPromptSha256": digest("test")}


def evidence_and_trace():
    evidence = [{"chunkId": "doc:1:0", "documentId": "doc", "documentVersion": 1,
                 "content": "甲\n乙丙", "documentContentHash": "frozen"}]
    identity = {"chunkId": "doc:1:0", "documentId": "doc", "documentVersion": 1, "kept": True}
    trace = {"characterBudget": [{**identity, "beforeChars": 4, "afterChars": 4}],
             "byteBudget": [{**identity, "beforeChars": 4, "afterChars": 3,
                             "beforeBytes": 10, "afterBytes": 7}],
             "model": {"called": True}, "configFingerprint": {"promptMaxBytes": 16384}}
    return evidence, trace


def test_restore_normalization_prefix_and_fingerprints():
    evidence, trace = evidence_and_trace()
    restored = restore_contexts(trace, evidence, " 问题 ")
    assert restored[0].content == "甲 乙"
    assert restored[0].source_sha256 == digest("甲\n乙丙")
    assert restored[0].content_sha256 == digest("甲 乙")


@pytest.mark.parametrize("mutation", ["missing", "length", "bytes", "version", "not_called", "duplicate"])
def test_restore_fails_closed_on_incomplete_evidence(mutation):
    evidence, trace = evidence_and_trace()
    if mutation == "missing":
        evidence.clear()
    elif mutation == "length":
        evidence[0]["content"] += "丁"
    elif mutation == "bytes":
        trace["byteBudget"][0]["afterBytes"] += 1
    elif mutation == "version":
        evidence[0]["documentVersion"] = 2
    elif mutation == "not_called":
        trace["model"]["called"] = False
    else:
        evidence.append(evidence[0].copy())
    with pytest.raises(Unreplayable):
        restore_contexts(trace, evidence, "问题")


@pytest.mark.parametrize("payload", [
    '{"answered":false,"answer":"缺乏依据","citedChunkIds":[]}',
    '{"answered":true,"answer":"答案","citedChunkIds":["unknown"]}',
    '{"answered":true,"answer":"答案","citedChunkIds":["doc:1:0","doc:1:0"]}',
    '{"answered":1,"answer":"答案","citedChunkIds":["doc:1:0"]}',
    '{"answered":false,"answered":true,"answer":"","citedChunkIds":[]}',
    '```json\n{"answered":false,"answer":"","citedChunkIds":[]}\n```',
    '{"answered":false,"answer":"","citedChunkIds":[],"extra":"value"}',
])
def test_invalid_contract_is_an_error_not_refusal(payload):
    with pytest.raises(ValueError):
        validate_answer(payload, [context()])


def test_valid_refusal_and_answer():
    assert validate_answer('{"answered":false,"answer":"","citedChunkIds":[]}', [context()])["answered"] is False
    assert validate_answer('{"answered":true,"answer":"答案","citedChunkIds":["doc:1:0"]}', [context()])["answered"] is True


def test_answer_length_matches_production_contract():
    payload = {"answered": True, "answer": "甲" * 4001, "citedChunkIds": ["doc:1:0"]}
    with pytest.raises(ValueError, match="production limit"):
        validate_answer(json.dumps(payload), [context()])


def test_schedule_has_exactly_three_interleaved_pairs_per_case():
    cases = [{"id": str(n)} for n in range(23)]
    calls = list(schedule(cases))
    assert len(calls) == 138
    for i in range(0, len(calls), 2):
        left, right = calls[i:i + 2]
        assert left[0] == right[0] and left[2] == right[2]
        assert {left[1], right[1]} == {"B0", "E1"}
    assert {v for _, v, _ in calls[::2]} == {"B0", "E1"}
    assert {(r, v, c["id"]) for r, v, c in calls} == {
        (r, v, str(i)) for r in (1, 2, 3) for v in ("B0", "E1") for i in range(23)}


def test_strict_v1_keeps_errors_and_duplicate_documents_failing():
    mapping = {"logical": {"documentId": "doc"}}
    assert strict_v1(case(), True, ["doc"], mapping)
    assert not strict_v1(case(), True, ["doc", "doc"], mapping)
    assert not strict_v1(case(), True, ["doc", "other"], mapping)
    assert strict_v1(case(safety=True), False, [], mapping)
    assert not strict_v1(case(safety=True), False, [], mapping, error=True)


@pytest.mark.parametrize("value", [None, {}, {"total_tokens": True}, {"total_tokens": -1}, {"total_tokens": "3"}])
def test_usage_missing_is_not_zero(value):
    normalized = normalize_usage(value)
    assert normalized is None or normalized["total_tokens"] is None


async def run_fake(tmp_path, handler, *, safety=False, max_calls=6, token_budget=100_000):
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await execute({"cases": [case(safety=safety)], "unreplayable": []}, tmp_path,
            model="fake", base_url="https://fake.example/v1", api_key="never-write-key",
            prompts={"B0": "base", "E1": "focused"}, max_calls=max_calls,
            token_budget=token_budget, max_tokens=128, client=client)


@pytest.mark.asyncio
async def test_requests_never_include_annotations_and_persist_each_result(tmp_path):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        assert (tmp_path / "events.jsonl").exists()
        return httpx.Response(200, json={"id": "provider", "model": "version",
            "choices": [{"finish_reason": "stop", "message": {"content":
                '{"answered":true,"answer":"答案","citedChunkIds":["doc:1:0"]}'}}],
            "usage": {"total_tokens": 25, "prompt_tokens": 20, "completion_tokens": 5}})
    rows = await run_fake(tmp_path, handler)
    assert len(rows) == 6 and all(r["strictPassed"] for r in rows)
    assert len((tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()) == 6
    for request in requests:
        user = json.loads(request["messages"][1]["content"])
        assert set(user) == {"question", "contexts"}
        assert "绝不泄漏" not in str(request)
    assert "never-write-key" not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.iterdir())
    assert len({r["requestSha256"] for r in rows}) == 2
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["automaticPromotionApproved"] is False
    assert summary["complete"] is True and summary["stopReason"] == "completed"
    assert summary["completedCalls"] == summary["plannedCalls"] == 6
    assert summary["missingCalls"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["http", "malformed", "truncated", "timeout", "tool_calls", "multiple_choices"])
async def test_errors_never_pass_safety_or_retry(tmp_path, mode):
    calls = []
    def handler(request):
        calls.append(request)
        if mode == "http":
            return httpx.Response(429, text="sensitive provider detail")
        if mode == "timeout":
            raise httpx.ReadTimeout("sensitive detail", request=request)
        content = "{}" if mode == "malformed" else '{"answered":false,"answer":"","citedChunkIds":[]}'
        choice = {"finish_reason": "length" if mode == "truncated" else "stop", "message": {"content": content}}
        if mode == "tool_calls":
            choice["message"]["tool_calls"] = [{"name": "unexpected"}]
        return httpx.Response(200, json={"choices": [choice, choice] if mode == "multiple_choices" else [choice]})
    rows = await run_fake(tmp_path, handler, safety=True, max_calls=1)
    assert len(calls) == len(rows) == 1
    assert rows[0]["status"] == "error" and rows[0]["answered"] is None
    assert rows[0]["strictPassed"] is False and rows[0]["retryCount"] == 0
    assert "sensitive" not in (tmp_path / "results.jsonl").read_text(encoding="utf-8")
    report = summarize(rows)
    assert all(s["safetyCorrect"] == 0 and s["usageMissing"] == 1 and s["knownTotalTokens"] is None
               for s in report["slices"])
    terminal = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert terminal["stopReason"] == "max_calls" and terminal["complete"] is False
    assert terminal["completedCalls"] == 1 and terminal["missingCalls"] == 5


@pytest.mark.asyncio
async def test_budget_stops_before_first_network_call(tmp_path):
    def handler(request):
        pytest.fail("budget must stop before network")
    rows = await run_fake(tmp_path, handler, token_budget=1)
    assert rows == []
    assert json.loads((tmp_path / "events.jsonl").read_text(encoding="utf-8"))["status"] == "budget_stopped"
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["plannedCalls"] == summary["missingCalls"] == 6
    assert summary["completedCalls"] == summary["reservedTokens"] == 0
    assert summary["complete"] is False and summary["stopReason"] == "token_budget"
    assert summary["slices"] == [] and summary["automaticPromotionApproved"] is False


@pytest.mark.asyncio
async def test_unreplayable_prevents_all_network_calls(tmp_path):
    with pytest.raises(Unreplayable):
        await execute({"unreplayable": [{"caseId": "missing"}]}, tmp_path,
            model="fake", base_url="https://fake.example", api_key="x", prompts={},
            max_calls=138, token_budget=2_000_000)


def test_refusal_candidate_pairs_keep_their_identity():
    # R1 不借用 E1 的名称，防止拒答修复被误记为引用聚焦策略的收益。
    planned = list(schedule([case()], ("B0", "R1")))
    assert len(planned) == 6 and {v for _, v, _ in planned} == {"B0", "R1"}
    rows = [dict(caseId="safe", repeatId=1, variant=v, category="safety",
                 questionType="direct", expectedAnswerable=False, status="ok",
                 strictPassed=passed, answered=not passed, latencyMs=1, usage=None)
            for v, passed in [("B0", False), ("R1", True)]]
    pair = summarize(rows)["pairedCases"][0]
    assert pair["gain"] and pair["R1"] and pair["R1Answered"] is False
    assert "E1" not in pair
