"""验证产品集的来源、拒答标签和真实文档映射，避免产生虚假的检索成绩。"""

from __future__ import annotations

import asyncio
import json
import math
import shutil
import sys
from pathlib import Path

import httpx
import pytest

from ai_service.orchestration.product_knowledge_evaluation import (
    DEFAULT_ROOT, QUOTAS, _answer_metrics, _candidate_metrics, _coverage_at_3,
    _read_new_traces, _trace_diagnostics,
    main, map_dataset, run_http, validate_corpus,
)
from ai_service.orchestration.customer_service_evaluation import (
    EvaluationDataset, EvaluationEntry, EvaluationResult,
)


def test_product_dataset_has_fixed_quotas_and_parseable_sources():
    dataset, report = validate_corpus()
    assert len(dataset.entries) == 90
    assert report["categoryCounts"] == QUOTAS
    assert len(report["documents"]) == 11
    assert all(row["chunkCount"] > 0 for row in report["documents"].values())
    assert report["liveMetrics"] is None
    assert sum(not row.expected_answerable for row in dataset.entries) == 9


def test_product_evaluation_refuses_empty_actual_document_map():
    dataset, report = validate_corpus()
    with pytest.raises(ValueError, match="PRODUCT_DOCUMENT_MAP_INCOMPLETE"):
        map_dataset(dataset, report, {"schemaVersion": "product-knowledge-document-map/v1", "documents": {}})


def test_mapping_preserves_no_answer_and_requires_hash_and_unique_actual_ids():
    dataset, report = validate_corpus()
    mapping = {
        "schemaVersion": "product-knowledge-document-map/v1",
        "documents": {
            logical: {"documentId": str(100 + i), "documentVersion": 1, "sha256": row["sha256"]}
            for i, (logical, row) in enumerate(report["documents"].items())
        },
    }
    mapped, versions = map_dataset(dataset, report, mapping)
    assert mapped.entries[0].expected_document_ids == ("100",)
    assert mapped.entries[-1].expected_document_ids == ()
    assert len(versions) == 11
    first = mapping["documents"]["01-platform-account"]
    first["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="PRODUCT_DOCUMENT_MAP_INVALID"):
        map_dataset(dataset, report, mapping)
    first["sha256"] = report["documents"]["01-platform-account"]["sha256"]
    first["documentId"] = "101"
    with pytest.raises(ValueError, match="PRODUCT_DOCUMENT_MAP_INVALID"):
        map_dataset(dataset, report, mapping)


@pytest.mark.parametrize("corruption", ["quota", "section", "planned"])
def test_invalid_labels_or_planning_cannot_enter_public_corpus(tmp_path: Path, corruption: str):
    root = tmp_path / "product"
    shutil.copytree(DEFAULT_ROOT, root)
    if corruption == "planned":
        path = root / "manifest.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["documents"][0]["path"] = "planning/product-specification.md"
    else:
        path = root / "evaluation/annotations.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        if corruption == "quota":
            raw["entries"][0]["category"] = "community"
        else:
            raw["entries"][0]["sourceSections"][0]["heading"] = "不存在的小节"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="PRODUCT_"):
        validate_corpus(root)


def _tiny_dataset() -> EvaluationDataset:
    return EvaluationDataset(
        "customer-service-rag-eval/v1", "test-v1", "test",
        (EvaluationEntry("case-1", "如何部署？", True, ("101",)),),
    )


def _trace(request_id: str, *, document_version: int = 3, provider: str = "remote_api") -> dict:
    rows = [{
        "rank": 1, "documentId": "101", "documentVersion": document_version, "score": 0.2,
    }]
    ranked = {
        **rows[0], "scoreSource": "retrieval_fallback" if provider == "disabled" else "reranker",
        "score": 0.8,
    }
    return {
        "schemaVersion": "customer-service-rag-trace/v1", "requestId": request_id,
        "traceId": "trace-1", "configFingerprint": {
            "retrievalTopK": 8, "finalTopK": 3, "minRerankScore": None,
            "promptMaxBytes": 16384, "maxContextChars": 1200,
            "maxTotalContextChars": 3000, "model": "model",
            "rerankerProvider": provider, "rerankerModel": "reranker",
            "embeddingModel": "embedding", "embeddingDimension": 3072, "alias": "alias",
        },
        "stageDurationsMs": {"retrieval": 1},
        "retrieved": rows, "filtered": [{**row, "kept": True} for row in rows],
        "reranked": [ranked], "thresholded": [{**ranked, "kept": True}],
        "selected": [{**ranked, "kept": True}],
        "characterBudget": [{**rows[0], "beforeChars": 1, "afterChars": 1, "kept": True}],
        "byteBudget": [{
            **rows[0], "beforeChars": 1, "afterChars": 1, "beforeBytes": 1, "afterBytes": 1, "kept": True,
        }],
        "model": {"called": True, "answered": True}, "citations": {"passed": True, "citedChunkIds": ["chunk-1"]},
        "terminal": {"outcome": "answered", "reasonCode": "ANSWERED", "degraded": False},
    }


def _trace_file(path: Path, *rows: dict) -> Path:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _append_trace(path: Path, *rows: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.writelines(json.dumps(row) + "\n" for row in rows)


@pytest.mark.asyncio
async def test_http_evaluation_uses_auth_and_same_request_trace_without_second_retrieval(tmp_path: Path):
    trace = _trace("run-1:case-1")
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-token"
        body = json.loads(request.content)
        assert body["requestId"] == "run-1:case-1"
        _append_trace(trace_path, trace)
        return httpx.Response(200, json={
            "requestId": body["requestId"], "answered": True, "answer": "已完成。",
            "degraded": False, "sources": [{"documentId": "101", "chunkId": "chunk-1"}],
        })

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=trace_path, token="test-token", timeout=3,
        transport=httpx.MockTransport(handler),
    )
    assert len(requests) == 1
    assert report["errorCount"] == report["diagnosticErrorCount"] == 0
    assert set(report["stageMetrics"]["retrieval"]) == {
        "recall_at_8", "mrr_at_3", "ndcg_at_3",
    }
    assert set(report["stageMetrics"]["reranked"]) == {
        "mrr_at_3", "ndcg_at_3", "coverage_at_3",
    }
    assert report["stageMetrics"]["retrieval"]["recall_at_8"] == 1.0
    assert report["stageMetrics"]["retrieval"]["mrr_at_3"] == 1.0
    assert report["stageMetrics"]["retrieval"]["ndcg_at_3"] == 1.0
    assert report["stageMetrics"]["reranked"]["mrr_at_3"] == 1.0
    assert report["stageMetrics"]["reranked"]["coverage_at_3"] == 1.0
    assert report["cases"][0]["finalContextDocumentIds"] == ("101",)
    assert report["cases"][0]["answer"] == "已完成。"
    assert report["answerMetrics"]["answerableAnswerRate"] == 1.0
    assert report["answerMetrics"]["strictCitationValidity"] == 1.0
    assert report["answerMetrics"]["httpLatencyMs"]["p95"] >= 0


@pytest.mark.asyncio
async def test_candidate_metrics_dedupe_repeated_document_chunks_before_top_three():
    dataset = _tiny_dataset()
    result = EvaluationResult(("102", "102", "102", "101"), True, ("101",), 1.0)
    assert _coverage_at_3(dataset, {"case-1": result}) == 1.0
    metrics = await _candidate_metrics(dataset, {"case-1": result})
    assert metrics == {
        "mrr_at_3": 0.5, "ndcg_at_3": 1 / math.log2(3), "coverage_at_3": 1.0,
    }


def test_trace_diagnostics_rejects_bad_durations_fields_and_duplicate_ranks():
    trace = _trace("run-1:case-1")
    trace["stageDurationsMs"] = {"retrieval": -1}
    trace["model"] = {"called": "yes", "answered": True}
    trace["citations"] = {"passed": True, "citedChunkIds": ["x" * 513]}
    trace["retrieved"] = [
        {"rank": 1, "documentId": "101", "documentVersion": 3, "score": 0.2},
        {"rank": 1, "documentId": "101", "documentVersion": 3, "score": 0.1},
    ]
    errors = _trace_diagnostics(trace, request_id="run-1:case-1", versions={"101": 3})
    assert "TRACE_DURATIONS_MISSING" in errors
    assert "TRACE_TERMINAL_DATA_MISSING" in errors
    assert "TRACE_STAGE_INVALID" in errors


def test_trace_reader_keeps_partial_jsonl_tail_for_next_read(tmp_path: Path):
    path = tmp_path / "trace.jsonl"
    encoded = json.dumps({"requestId": "run-1:case-1"}).encode()
    path.write_bytes(encoded[:10])
    first, offset = _read_new_traces(path, 0)
    assert first == {} and offset == 0
    with path.open("ab") as stream:
        stream.write(encoded[10:] + b"\n")
    second, offset = _read_new_traces(path, offset)
    assert list(second) == ["run-1:case-1"] and offset == path.stat().st_size


@pytest.mark.parametrize("duration", [-1, True, float("nan"), float("inf"), "1"])
def test_trace_durations_require_nonnegative_finite_numbers(duration):
    trace = _trace("run-1:case-1")
    trace["stageDurationsMs"] = {"retrieval": duration}
    assert "TRACE_DURATIONS_MISSING" in _trace_diagnostics(
        trace, request_id="run-1:case-1", versions={"101": 3},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,error", [
    ("configFingerprint", [], "TRACE_CONFIG_MISSING"),
    ("model", [], "TRACE_TERMINAL_DATA_MISSING"),
    ("model", {}, "TRACE_TERMINAL_DATA_MISSING"),
    ("citations", {"passed": None}, "TRACE_TERMINAL_DATA_MISSING"),
    ("citations", {"passed": True, "citedChunkIds": [42]}, "TRACE_TERMINAL_DATA_MISSING"),
])
async def test_malformed_trace_objects_fail_closed_without_crashing(tmp_path: Path, field, value, error):
    path = _trace_file(tmp_path / "trace.jsonl")
    trace = _trace("run-1:case-1")
    trace[field] = value

    def handler(request: httpx.Request) -> httpx.Response:
        _append_trace(path, trace)
        return httpx.Response(200, json={
            "requestId": "run-1:case-1", "answered": True, "answer": "答复",
            "degraded": False, "sources": [],
        })

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=path, token="token", timeout=3, transport=httpx.MockTransport(handler),
    )
    assert error in report["cases"][0]["diagnosticErrors"]
    assert all(value is None for value in report["stageMetrics"].values())


@pytest.mark.asyncio
async def test_success_waits_for_delayed_partial_trace_without_retry(tmp_path: Path):
    path = _trace_file(tmp_path / "trace.jsonl")
    trace_bytes = (json.dumps(_trace("run-1:case-1")) + "\n").encode()
    calls = 0

    async def finish_trace():
        await asyncio.sleep(0.06)
        with path.open("ab") as stream:
            stream.write(trace_bytes[30:])

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        path.write_bytes(trace_bytes[:30])
        asyncio.create_task(finish_trace())
        return httpx.Response(200, json={
            "requestId": "run-1:case-1", "answered": True, "answer": "答复",
            "degraded": False, "sources": [{"documentId": "101", "excerpt": "完整人工复核摘录"}],
        })

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=path, token="token", timeout=3, transport=httpx.MockTransport(handler),
    )
    assert calls == 1 and report["diagnosticErrorCount"] == 0
    assert report["cases"][0]["sources"][0]["excerpt"] == "完整人工复核摘录"
    assert report["cases"][0]["latencyMs"] < 60


def test_answer_metrics_keeps_faults_in_denominator_and_strict_duplicate_citation_rule():
    dataset = EvaluationDataset("customer-service-rag-eval/v1", "v1", "", (
        EvaluationEntry("answered", "问题", True, ("101",)),
        EvaluationEntry("failed", "问题", True, ("101",)),
        EvaluationEntry("refused", "问题", False, ()),
        EvaluationEntry("fallback", "问题", False, ()),
    ))
    metrics = _answer_metrics(dataset, [
        {"id": "answered", "answered": True, "sources": [{"documentId": "101"}, {"documentId": "101"}], "latencyMs": 1},
        {"id": "failed", "error": "PRODUCT_HTTP_REQUEST_FAILED", "latencyMs": 2},
        {"id": "refused", "answered": False, "sources": [], "latencyMs": 3, "degraded": True},
        {"id": "fallback", "error": "PRODUCT_ANSWER_SERVICE_FAILED", "latencyMs": 4},
    ])
    assert metrics["answerableAnswerRate"] == metrics["safeRefusalRate"] == 0.5
    assert metrics["strictCitationValidity"] == 0.25
    assert metrics["errorCount"] == 2 and metrics["degradedCount"] == 1
    assert metrics["httpLatencyMs"]["mean"] == 2.5
    assert metrics["httpLatencyMs"]["p95"] == pytest.approx(3.85)


def test_filtered_rank_gaps_are_valid():
    trace = _trace("run-1:case-1")
    trace["filtered"] = [
        {"rank": 1, "documentId": "101", "documentVersion": 3, "score": 0.2, "kept": True},
        {"rank": 3, "documentId": "101", "documentVersion": 3, "score": 0.1, "kept": True},
    ]
    assert _trace_diagnostics(trace, request_id="run-1:case-1", versions={"101": 3}) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,corruption", [
    *[(stage, "reversed_rank") for stage in ("retrieved", "filtered", "reranked", "thresholded", "selected", "characterBudget", "byteBudget")],
    *[(stage, "missing_kept") for stage in ("filtered", "thresholded", "selected", "characterBudget", "byteBudget")],
    *[(stage, "string_kept") for stage in ("filtered", "thresholded", "selected", "characterBudget", "byteBudget")],
    ("retrieved", "unexpected_kept"), ("reranked", "unexpected_kept"),
    ("characterBudget", "inconsistent_kept"), ("byteBudget", "inconsistent_kept"),
    ("byteBudget", "missing_bytes"), ("byteBudget", "negative_bytes"),
    ("byteBudget", "inconsistent_bytes"),
])
async def test_rank_order_and_kept_contract_errors_clear_all_stage_metrics(tmp_path: Path, stage, corruption):
    path = _trace_file(tmp_path / "trace.jsonl")
    trace = _trace("run-1:case-1")
    row = trace[stage][0]
    if corruption == "reversed_rank":
        trace[stage] = [{**row, "rank": 2}, {**row, "rank": 1}]
    elif corruption == "missing_kept":
        del row["kept"]
    elif corruption == "string_kept":
        row["kept"] = "false"
    elif corruption in {"unexpected_kept", "inconsistent_kept"}:
        row["kept"] = False
    elif corruption == "missing_bytes":
        del row["afterBytes"]
    elif corruption == "negative_bytes":
        row["beforeBytes"] = -1
    else:
        row["afterBytes"] = 0

    def handler(request: httpx.Request) -> httpx.Response:
        _append_trace(path, trace)
        return httpx.Response(200, json={
            "requestId": "run-1:case-1", "answered": True, "answer": "答复",
            "degraded": False, "sources": [],
        })

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=path, token="token", timeout=3, transport=httpx.MockTransport(handler),
    )
    assert "TRACE_STAGE_INVALID" in report["cases"][0]["diagnosticErrors"]
    assert all(value is None for value in report["stageMetrics"].values())


def test_http_cli_requires_ignored_output_and_prints_no_case_body(tmp_path: Path, monkeypatch, capsys):
    import ai_service.orchestration.product_knowledge_evaluation as module

    output_root = tmp_path / "tmp"
    output_root.mkdir()
    trace_file = _trace_file(tmp_path / "trace.jsonl")
    document_map = tmp_path / "map.json"
    document_map.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(module, "EVALUATION_OUTPUT_ROOT", output_root)
    monkeypatch.setattr(module, "map_dataset", lambda *args: (_tiny_dataset(), {"101": 3}))

    async def fake_http(*args, **kwargs):
        return {"sampleCount": 1, "cases": [{"answer": "仅供受控人工复核", "sources": []}]}

    monkeypatch.setattr(module, "run_http", fake_http)
    arguments = [
        "product_knowledge_evaluation", "--http-base-url", "http://rag.test", "--run-id", "run-1",
        "--document-map", str(document_map), "--trace-file", str(trace_file),
    ]
    monkeypatch.setattr(sys, "argv", [*arguments, "--output", str(tmp_path / "outside.json")])
    with pytest.raises(SystemExit):
        main()
    assert not (tmp_path / "outside.json").exists()
    output = output_root / "report.json"
    monkeypatch.setattr(sys, "argv", [*arguments, "--output", str(output)])
    main()
    assert "仅供受控人工复核" in output.read_text(encoding="utf-8")
    assert "仅供受控人工复核" not in capsys.readouterr().out


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("trace_rows", "version", "provider", "degraded", "fallback", "expected"),
    [
        ((), 3, "remote_api", False, False, "TRACE_MISSING"),
        ((1, 2), 3, "remote_api", False, False, "TRACE_DUPLICATE"),
        ((1,), 4, "remote_api", False, False, "PRODUCT_INDEX_VERSION_MISMATCH"),
        ((1,), 3, "disabled", True, False, None),
        ((1,), 3, "remote_api", False, True, "PRODUCT_ANSWER_SERVICE_FAILED"),
    ],
)
async def test_http_diagnostics_fail_closed_for_trace_mismatch_and_fallback(
    tmp_path: Path, trace_rows, version, provider, degraded, fallback, expected,
):
    entries = [
        _trace("run-1:case-1", document_version=version, provider=provider)
        for _ in range(trace_rows[0] if trace_rows else 0)
    ]
    if len(trace_rows) == 2:
        entries.append(_trace("run-1:case-1", document_version=version, provider=provider))
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        _append_trace(trace_path, *entries)
        return httpx.Response(200, json={
            "requestId": body["requestId"], "answered": not fallback,
            "answer": "系统暂时无法生成可靠回答，请稍后重试。" if fallback else "答复",
            "degraded": degraded, "sources": [],
        })

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=trace_path, token="test-token", timeout=3,
        transport=httpx.MockTransport(handler),
    )
    if expected:
        errors = report["cases"][0].get("diagnosticErrors", [])
        assert expected in errors or report["cases"][0].get("error") == expected
        assert all(value is None for value in report["stageMetrics"].values())
    if provider == "disabled" or degraded:
        assert report["stageMetrics"]["reranked"] is None
        assert report["trueRerankMetricsAvailable"] is False


@pytest.mark.asyncio
async def test_missing_rerank_stage_does_not_hide_raw_retrieval_metric(tmp_path: Path):
    trace = _trace("run-1:case-1")
    del trace["reranked"]
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        _append_trace(trace_path, trace)
        return httpx.Response(200, json={
            "requestId": body["requestId"], "answered": True, "answer": "答复",
            "degraded": False, "sources": [],
        })

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=trace_path, token="test-token", timeout=3,
        transport=httpx.MockTransport(handler),
    )
    assert report["stageMetrics"]["retrieval"]["recall_at_8"] == 1.0
    assert report["stageMetrics"]["reranked"] is None
    assert "TRACE_STAGE_MISSING:reranked" in report["cases"][0]["diagnosticErrors"]


@pytest.mark.asyncio
async def test_http_evaluation_rejects_mixed_configuration_fingerprints(tmp_path: Path):
    dataset = EvaluationDataset(
        "customer-service-rag-eval/v1", "test-v1", "test",
        (
            EvaluationEntry("case-1", "问题一", True, ("101",)),
            EvaluationEntry("case-2", "问题二", True, ("101",)),
        ),
    )
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        trace = _trace(body["requestId"])
        if body["requestId"].endswith("case-2"):
            trace["configFingerprint"]["model"] = "different-model"
        _append_trace(trace_path, trace)
        return httpx.Response(200, json={
            "requestId": body["requestId"], "answered": True, "answer": "答复",
            "degraded": False, "sources": [],
        })

    report = await run_http(
        dataset, {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=trace_path, token="test-token", timeout=3,
        transport=httpx.MockTransport(handler),
    )
    assert report["configFingerprint"] is None
    assert all("TRACE_CONFIG_MISMATCH" in row["diagnosticErrors"] for row in report["cases"])
    assert all(value is None for value in report["stageMetrics"].values())


@pytest.mark.asyncio
async def test_http_fallback_preserves_unique_service_error_trace_and_degraded_count(tmp_path: Path):
    path = _trace_file(tmp_path / "trace.jsonl")
    trace = _trace("run-1:case-1")
    trace["terminal"] = {
        "outcome": "service_error", "reasonCode": "MODEL_PARSE_FAILED", "degraded": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        _append_trace(path, trace)
        return httpx.Response(200, json={
            "requestId": "run-1:case-1", "answered": False,
            "answer": "系统暂时无法生成可靠回答，请稍后重试。",
            "degraded": False, "sources": [],
        })

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=path, token="token", timeout=3, transport=httpx.MockTransport(handler),
    )
    case = report["cases"][0]
    assert case["error"] == "PRODUCT_ANSWER_SERVICE_FAILED"
    assert case["diagnosticErrors"] == []
    assert case["terminal"]["reasonCode"] == "MODEL_PARSE_FAILED"
    assert case["degraded"] is True
    assert report["errorCount"] == report["fallbackCount"] == report["degradedCount"] == 1
    assert report["answerMetrics"]["degradedCount"] == 1
    assert all(value is None for value in report["stageMetrics"].values())


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome,reason", [("service_error", "MODEL_PARSE_FAILED"), ("cancelled", "TIMEOUT")])
async def test_normal_http_response_rejects_conflicting_error_terminal(tmp_path: Path, outcome, reason):
    path = _trace_file(tmp_path / "trace.jsonl")
    trace = _trace("run-1:case-1")
    trace["terminal"] = {"outcome": outcome, "reasonCode": reason, "degraded": False}

    def handler(request: httpx.Request) -> httpx.Response:
        _append_trace(path, trace)
        return httpx.Response(200, json={
            "requestId": "run-1:case-1", "answered": True, "answer": "答复",
            "degraded": False, "sources": [],
        })

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=path, token="token", timeout=3, transport=httpx.MockTransport(handler),
    )
    assert "TRACE_TERMINAL_MISMATCH" in report["cases"][0]["diagnosticErrors"]
    assert all(value is None for value in report["stageMetrics"].values())


@pytest.mark.asyncio
async def test_http_failure_records_safe_trace_terminal_without_response_body(tmp_path: Path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")
    trace = _trace("run-1:case-1")
    trace["terminal"] = {"outcome": "service_error", "reasonCode": "MODEL_CALL_FAILED", "degraded": False}

    def handler(request: httpx.Request) -> httpx.Response:
        _append_trace(trace_path, trace)
        return httpx.Response(503, text="sensitive provider response")

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=trace_path, token="test-token", timeout=3,
        transport=httpx.MockTransport(handler),
    )
    case = report["cases"][0]
    assert case["error"] == "PRODUCT_HTTP_REQUEST_FAILED"
    assert case["terminal"]["reasonCode"] == "MODEL_CALL_FAILED"
    assert "sensitive provider response" not in json.dumps(report)
    assert report["httpErrorCount"] == 1
    assert all(value is None for value in report["stageMetrics"].values())


@pytest.mark.asyncio
async def test_http_timeout_collects_cancellation_terminal(tmp_path: Path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")
    trace = _trace("run-1:case-1")
    trace["terminal"] = {"outcome": "cancelled", "reasonCode": "TIMEOUT", "degraded": False}

    def handler(request: httpx.Request) -> httpx.Response:
        _append_trace(trace_path, trace)
        raise httpx.ReadTimeout("secret detail", request=request)

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=trace_path, token="test-token", timeout=3,
        transport=httpx.MockTransport(handler),
    )
    assert report["cases"][0]["error"] == "PRODUCT_CASE_TIMEOUT"
    assert report["cases"][0]["terminal"]["reasonCode"] == "TIMEOUT"
    assert "secret detail" not in json.dumps(report)


@pytest.mark.asyncio
async def test_malformed_trace_rows_and_terminal_fields_fail_closed(tmp_path: Path):
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text("", encoding="utf-8")
    trace = _trace("run-1:case-1")
    trace["retrieved"] = [{}]
    trace["terminal"]["reasonCode"] = "sensitive detail"
    trace["terminal"]["degraded"] = "false"

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        _append_trace(trace_path, trace)
        return httpx.Response(200, json={
            "requestId": body["requestId"], "answered": True, "answer": "答复",
            "degraded": False, "sources": [],
        })

    report = await run_http(
        _tiny_dataset(), {"101": 3}, base_url="http://rag.test", run_id="run-1",
        trace_file=trace_path, token="test-token", timeout=3,
        transport=httpx.MockTransport(handler),
    )
    errors = report["cases"][0]["diagnosticErrors"]
    assert "TRACE_STAGE_INVALID" in errors
    assert "TRACE_INCOMPLETE" in errors
    assert all(value is None for value in report["stageMetrics"].values())
    assert "sensitive detail" not in json.dumps(report)


def test_existing_http_output_is_rejected_before_any_request(tmp_path: Path, monkeypatch):
    output = tmp_path / "existing.json"
    output.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        "product_knowledge_evaluation", "--http-base-url", "http://127.0.0.1:8000",
        "--run-id", "run-1", "--document-map", str(tmp_path / "map.json"),
        "--trace-file", str(tmp_path / "trace.jsonl"), "--output", str(output),
    ])
    with pytest.raises(SystemExit):
        main()
    assert output.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--live", "--run-id", "run-1"],
        ["--trace-file", "trace.jsonl"],
        ["--case-ids", "case-1"],
    ],
)
def test_http_only_arguments_are_rejected_outside_http_mode(arguments, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["product_knowledge_evaluation", *arguments])
    with pytest.raises(SystemExit):
        main()


@pytest.mark.asyncio
async def test_http_run_rejects_overlong_request_id_before_transport(tmp_path: Path):
    dataset = EvaluationDataset(
        "customer-service-rag-eval/v1", "test-v1", "test",
        (EvaluationEntry("x" * 123, "问题", True, ("101",)),),
    )
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    with pytest.raises(ValueError, match="PRODUCT_REQUEST_ID_TOO_LONG"):
        await run_http(
            dataset, {"101": 3}, base_url="http://rag.test", run_id="run-1",
            trace_file=tmp_path / "missing.jsonl", token="token", timeout=3,
            transport=httpx.MockTransport(handler),
        )
    assert not called
