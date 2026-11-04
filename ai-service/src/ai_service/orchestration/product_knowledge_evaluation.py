"""产品知识校验与真实 RAG 评测入口；默认只解析本地公共知识正文。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_service.config import Settings
from ai_service.orchestration.customer_service_evaluation import (
    EvaluationDataset, EvaluationResult, _latency_summary, _unique,
    evaluate_customer_service, load_evaluation_dataset,
)
from ai_service.orchestration.document_etl import parse_document, split_sections


DEFAULT_ROOT = Path(__file__).resolve().parents[4] / "doc/knowledge/product"
EVALUATION_OUTPUT_ROOT = Path(__file__).resolve().parents[4] / "tmp"
QUOTAS = {
    "platform-account": 8, "app-generation": 24, "preview-history": 10,
    "deployment-download": 14, "community": 14, "admin-knowledge": 5,
    "boundaries-safety": 15,
}


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_corpus(root: Path = DEFAULT_ROOT) -> tuple[EvaluationDataset, dict[str, Any]]:
    """固定配额、文档引用和小节锚点均通过后才允许连接真实依赖。"""
    root = root.resolve()
    manifest = _json(root / "manifest.json")
    dataset = load_evaluation_dataset(root / "evaluation/dataset.json")
    annotations = _json(root / "evaluation/annotations.json")
    if len(dataset.entries) != 90 or annotations["categoryQuotas"] != QUOTAS:
        raise ValueError("PRODUCT_EVALUATION_QUOTA_INVALID")
    if annotations["datasetVersion"] != dataset.dataset_version:
        raise ValueError("PRODUCT_EVALUATION_VERSION_MISMATCH")
    if len({entry.question for entry in dataset.entries}) != 90:
        raise ValueError("PRODUCT_EVALUATION_QUESTION_DUPLICATED")
    labels = {row["id"]: row for row in annotations["entries"]}
    if len(labels) != 90 or set(labels) != {entry.id for entry in dataset.entries}:
        raise ValueError("PRODUCT_EVALUATION_ANNOTATIONS_INVALID")
    counts = dict.fromkeys(QUOTAS, 0)
    documents: dict[str, Any] = {}
    # 离线检查不加载 .env，更不初始化模型或向量存储。
    settings = Settings(_env_file=None, customer_service_rag_enabled=False)
    for row in manifest["documents"]:
        path = (root / row["path"]).resolve()
        if (
            not path.is_relative_to(root / "documents") or path.suffix != ".md"
            or row["id"] in documents or row["contentStatus"] not in {"current", "recommended"}
            or not row["sources"]
        ):
            raise ValueError("PRODUCT_KNOWLEDGE_DOCUMENT_INVALID")
        text = path.read_text(encoding="utf-8")
        if "【规划" in text or not re.search(r"^## .+【(?:当前功能|推荐规范)", text, re.M):
            raise ValueError("PRODUCT_KNOWLEDGE_STATUS_INVALID")
        sections = parse_document(path, "md")
        chunks = split_sections(
            sections, document_id=row["id"], document_version=1,
            source_name=path.name, settings=settings,
        )
        documents[row["id"]] = {
            "path": row["path"], "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "sectionCount": len(sections), "chunkCount": len(chunks),
            "characters": len(text), "headings": re.findall(r"^## (.+)$", text, re.M),
        }
    if len(documents) != 11:
        raise ValueError("PRODUCT_KNOWLEDGE_DOCUMENT_COUNT_INVALID")
    for entry in dataset.entries:
        label = labels[entry.id]
        category = label["category"]
        if category not in counts or not label["expectedFacts"] or not label["reviewStatus"]:
            raise ValueError("PRODUCT_EVALUATION_LABEL_INVALID")
        counts[category] += 1
        refs = label["sourceSections"]
        if set(entry.expected_document_ids) != {ref["documentId"] for ref in refs}:
            raise ValueError("PRODUCT_EVALUATION_SOURCE_MISMATCH")
        for ref in refs:
            doc = documents.get(ref["documentId"])
            if doc is None or not any(h.startswith(ref["heading"] + "【") for h in doc["headings"]):
                raise ValueError("PRODUCT_EVALUATION_SECTION_MISSING")
    if counts != QUOTAS:
        raise ValueError("PRODUCT_EVALUATION_QUOTA_INVALID")
    return dataset, {
        "mode": "local-validation", "datasetVersion": dataset.dataset_version,
        "sampleCount": 90, "categoryCounts": counts, "documents": documents,
        "reviewStatus": annotations["reviewStatus"],
        "liveMetrics": None,
    }


def map_dataset(
    dataset: EvaluationDataset, report: dict[str, Any], mapping: dict[str, Any],
) -> tuple[EvaluationDataset, dict[str, int]]:
    """逻辑 ID 仅用于内容编写；真实评测要求实际 ID、版本及正文摘要齐备。"""
    if mapping.get("schemaVersion") != "product-knowledge-document-map/v1":
        raise ValueError("PRODUCT_DOCUMENT_MAP_INVALID")
    rows = mapping.get("documents", {})
    if set(rows) != set(report["documents"]):
        raise ValueError("PRODUCT_DOCUMENT_MAP_INCOMPLETE")
    actual: dict[str, str] = {}
    versions: dict[str, int] = {}
    for logical, row in rows.items():
        value, version = row.get("documentId"), row.get("documentVersion")
        if (
            not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", value)
            or value in versions or type(version) is not int or version < 1
            or row.get("sha256") != report["documents"][logical]["sha256"]
        ):
            raise ValueError("PRODUCT_DOCUMENT_MAP_INVALID")
        actual[logical], versions[value] = value, version
    return replace(dataset, entries=tuple(
        replace(entry, expected_document_ids=tuple(actual[v] for v in entry.expected_document_ids))
        for entry in dataset.entries
    )), versions


class _RecordingStore:
    """只代理读取，保留本次问答实际使用的原始候选顺序和版本。"""

    def __init__(self, store: Any, versions: dict[str, int]):
        self.store, self.versions = store, versions
        self.retrieved: tuple[str, ...] = ()
        self.version_mismatch = False

    async def search(self, vector: list[float], limit: int) -> Any:
        rows = await self.store.search(vector, limit)
        for row in rows:
            if row.document_id in self.versions and row.document_version != self.versions[row.document_id]:
                self.version_mismatch = True
                raise ValueError("PRODUCT_INDEX_VERSION_MISMATCH")
        self.retrieved = tuple(row.document_id for row in rows)
        return rows


class _RecordingReranker:
    """记录同一次回答中的重排顺序，不额外调用模型或修改分数。"""

    def __init__(self, reranker: Any):
        self.reranker = reranker
        self.ranked: tuple[str, ...] = ()

    async def rerank(self, question: str, candidates: Any, *, top_n: int) -> Any:
        rows = await self.reranker.rerank(question, candidates, top_n=top_n)
        self.ranked = tuple(row.chunk.document_id for row in rows)
        return rows


TRACE_STAGES = (
    "retrieved", "filtered", "reranked", "thresholded", "selected",
    "characterBudget", "byteBudget",
)
TRACE_TERMINALS = {"answered", "refused", "service_error", "cancelled"}
SYSTEM_FALLBACK_TEXT = "系统暂时无法生成可靠回答，请稍后重试。"
TRACE_CONFIG_FIELDS = {
    "retrievalTopK", "finalTopK", "minRerankScore", "promptMaxBytes",
    "maxContextChars", "maxTotalContextChars", "model", "rerankerProvider",
    "rerankerModel", "embeddingModel", "embeddingDimension", "alias",
}
TRACE_REASON_CODES = {
    "CUSTOMER_SERVICE_UNAVAILABLE", "CUSTOMER_SERVICE_INVALID_REQUEST",
    "CUSTOMER_SERVICE_EMBEDDING_UNAVAILABLE", "CUSTOMER_SERVICE_VECTOR_STORE_UNAVAILABLE",
    "RAW_RETRIEVAL_EMPTY", "FILTERED_EMPTY", "RERANK_EMPTY", "THRESHOLD_EMPTY",
    "RERANK_UNAVAILABLE_WITH_THRESHOLD", "CHAR_CONTEXT_EMPTY", "PROMPT_BUDGET_EXHAUSTED",
    "BYTE_CONTEXT_EMPTY", "MODEL_REFUSED", "MODEL_CALL_FAILED", "MODEL_PARSE_FAILED",
    "CITATION_INVALID", "ANSWERED", "TIMEOUT", "CLIENT_DISCONNECTED", "CANCELLED",
}


def _read_new_traces(path: Path, offset: int) -> tuple[dict[str, list[dict[str, Any]]], int]:
    traces: dict[str, list[dict[str, Any]]] = {}
    if not path.is_file():
        return traces, offset
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        if size < offset:
            offset = 0
        stream.seek(offset)
        payload = stream.read()
        # 只消费已写完的 JSONL 行；尾行可能仍在追加，不能永久跳过。
        complete_length = payload.rfind(b"\n") + 1
        offset += complete_length
    for line in payload[:complete_length].decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict) and isinstance(value.get("requestId"), str):
            traces.setdefault(value["requestId"], []).append(value)
    return traces, offset


def _stage_document_ids(records: Any) -> tuple[str, ...]:
    if not isinstance(records, list):
        return ()
    result, seen = [], set()
    for row in records:
        if not isinstance(row, dict):
            continue
        if row.get("kept") is False:
            continue
        value = row.get("documentId")
        if (
            isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,19}", value)
            and value not in seen
        ):
            seen.add(value)
            result.append(value)
    return tuple(result)


def _terminal_summary(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    outcome, reason, degraded = value.get("outcome"), value.get("reasonCode"), value.get("degraded")
    if (
        not isinstance(outcome, str) or outcome not in TRACE_TERMINALS
        or not isinstance(reason, str) or reason not in TRACE_REASON_CODES
        or not isinstance(degraded, bool)
    ):
        return None
    return {"outcome": outcome, "reasonCode": reason, "degraded": degraded}


def _rerank_records(rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    result = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        document_id = row.get("documentId")
        rank, version, score = row.get("rank"), row.get("documentVersion"), row.get("score")
        if not isinstance(document_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", document_id):
            continue
        item: dict[str, Any] = {"documentId": document_id}
        if type(rank) is int and 1 <= rank <= 100:
            item["rank"] = rank
        if type(version) is int and version >= 1:
            item["documentVersion"] = version
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            try:
                if math.isfinite(float(score)):
                    item["score"] = float(score)
            except (OverflowError, ValueError):
                pass
        if row.get("scoreSource") in ("reranker", "retrieval_fallback"):
            item["scoreSource"] = row["scoreSource"]
        result.append(item)
    return result


def _source_summaries(rows: list[Any]) -> list[dict[str, Any]]:
    result = []
    for source in rows:
        if not isinstance(source, dict):
            continue
        document_id, version, chunk_id = (
            source.get("documentId"), source.get("documentVersion"), source.get("chunkId"),
        )
        if (
            not isinstance(document_id, str)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", document_id)
            or type(version) is not int or version < 1
            or not isinstance(chunk_id, str) or not chunk_id or len(chunk_id) > 512
        ):
            continue
        result.append({
            "documentId": document_id, "documentVersion": version, "chunkId": chunk_id,
        })
    return result


def _finite_score(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


def _trace_diagnostics(
    trace: dict[str, Any] | None, *, request_id: str, versions: dict[str, int],
) -> list[str]:
    if trace is None:
        return ["TRACE_MISSING"]
    errors = []
    if trace.get("schemaVersion") != "customer-service-rag-trace/v1":
        errors.append("TRACE_SCHEMA_INVALID")
    if trace.get("requestId") != request_id:
        errors.append("TRACE_REQUEST_ID_MISMATCH")
    fingerprint = trace.get("configFingerprint")
    if not isinstance(fingerprint, dict) or not TRACE_CONFIG_FIELDS <= set(fingerprint):
        errors.append("TRACE_CONFIG_MISSING")
    durations = trace.get("stageDurationsMs")
    if not isinstance(durations, dict) or any(
        not _finite_score(value) or value < 0 for value in durations.values()
    ):
        errors.append("TRACE_DURATIONS_MISSING")
    model, citations = trace.get("model"), trace.get("citations")
    if (
        not isinstance(model, dict) or not {"called", "answered"} <= set(model)
        or type(model.get("called")) is not bool
        or model.get("answered") is not None and type(model.get("answered")) is not bool
        or not isinstance(citations, dict) or not {"passed", "citedChunkIds"} <= set(citations)
        or citations.get("passed") is not None and type(citations.get("passed")) is not bool
        or not isinstance(citations.get("citedChunkIds"), list)
        or any(not isinstance(value, str) or not value or len(value) > 512
               for value in citations.get("citedChunkIds", []))
    ):
        errors.append("TRACE_TERMINAL_DATA_MISSING")
    terminal = trace.get("terminal")
    if _terminal_summary(terminal) is None:
        errors.append("TRACE_INCOMPLETE")
    missing_stages = []
    for stage in TRACE_STAGES:
        if not isinstance(trace.get(stage), list):
            missing_stages.append(stage)
    errors.extend(f"TRACE_STAGE_MISSING:{stage}" for stage in missing_stages)
    for stage in TRACE_STAGES:
        rows = trace.get(stage)
        if not isinstance(rows, list):
            continue
        previous_rank = 0
        for row in rows:
            if not isinstance(row, dict):
                errors.append("TRACE_STAGE_INVALID")
                break
            document_id = row.get("documentId")
            version = row.get("documentVersion")
            rank = row.get("rank")
            if (
                not isinstance(document_id, str)
                or not re.fullmatch(r"[1-9][0-9]{0,19}", document_id)
                or type(version) is not int or version < 1
                or type(rank) is not int or not 1 <= rank <= 100 or rank <= previous_rank
            ):
                errors.append("TRACE_STAGE_INVALID")
                break
            # 排名允许空缺，但列表必须按 rank 严格递增，避免数组顺序伪造指标。
            previous_rank = rank
            if (
                stage in {"filtered", "thresholded", "selected", "characterBudget", "byteBudget"}
                and type(row.get("kept")) is not bool
                or stage in {"retrieved", "reranked"} and "kept" in row
            ):
                errors.append("TRACE_STAGE_INVALID")
                break
            if stage in {"retrieved", "filtered"} and not _finite_score(row.get("score")):
                errors.append("TRACE_STAGE_INVALID")
                break
            if stage in {"reranked", "thresholded", "selected"} and (
                not _finite_score(row.get("score"))
                or row.get("scoreSource") not in ("reranker", "retrieval_fallback")
            ):
                errors.append("TRACE_STAGE_INVALID")
                break
            if stage in {"characterBudget", "byteBudget"} and (
                type(row.get("beforeChars")) is not int or row["beforeChars"] < 0
                or type(row.get("afterChars")) is not int or row["afterChars"] < 0
                or row["afterChars"] > row["beforeChars"]
                or row["kept"] != (row["afterChars"] > 0)
            ):
                errors.append("TRACE_STAGE_INVALID")
                break
            if stage == "byteBudget" and (
                type(row.get("beforeBytes")) is not int or row["beforeBytes"] < 0
                or type(row.get("afterBytes")) is not int or row["afterBytes"] < 0
                or row["afterBytes"] > row["beforeBytes"]
                or row["kept"] != (row["afterBytes"] > 0)
            ):
                errors.append("TRACE_STAGE_INVALID")
                break
            if isinstance(document_id, str) and document_id in versions and version != versions[document_id]:
                errors.append("PRODUCT_INDEX_VERSION_MISMATCH")
                break
        if "PRODUCT_INDEX_VERSION_MISMATCH" in errors:
            break
    return list(dict.fromkeys(errors))


async def run_http(
    dataset: EvaluationDataset, versions: dict[str, int], *, base_url: str,
    run_id: str, trace_file: Path, token: str, timeout: float,
    transport: Any = None,
) -> dict[str, Any]:
    """通过现有 HTTP 服务问答，并把本次请求的白名单 trace 关联到逐题报告。"""
    import httpx

    if any(len(f"{run_id}:{entry.id}") > 128 for entry in dataset.entries):
        raise ValueError("PRODUCT_REQUEST_ID_TOO_LONG")
    cases: list[dict[str, Any]] = []
    results: dict[str, dict[str, EvaluationResult]] = {
        stage: {} for stage in ("retrieval", "reranked", "thresholded", "selected", "finalContext")
    }
    fingerprints: list[dict[str, Any]] = []
    trace_offset = trace_file.stat().st_size if trace_file.is_file() else 0

    async def collect_trace(request_id: str, *, wait_seconds: float = 0.5) -> list[dict[str, Any]]:
        nonlocal trace_offset
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while True:
            new_traces, trace_offset = _read_new_traces(trace_file, trace_offset)
            matches = new_traces.get(request_id, [])
            if matches or asyncio.get_running_loop().time() >= deadline:
                return matches
            await asyncio.sleep(0.05)

    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"), timeout=timeout, transport=transport,
        follow_redirects=False, trust_env=False,
    ) as client:
        for entry in dataset.entries:
            request_id = f"{run_id}:{entry.id}"
            case: dict[str, Any] = {"id": entry.id, "requestId": request_id}
            if len(request_id) > 128:
                cases.append({**case, "error": "PRODUCT_REQUEST_ID_TOO_LONG"})
                continue
            try:
                started = time.perf_counter()
                response = await client.post(
                    "/internal/v1/customer-service/answers",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"requestId": request_id, "question": entry.question},
                )
                # HTTP 延迟不含随后为日志落盘等待的时间。
                case["latencyMs"] = round((time.perf_counter() - started) * 1000, 3)
                matches = await collect_trace(request_id)
                if response.status_code != 200:
                    case["error"] = "PRODUCT_HTTP_REQUEST_FAILED"
                    case["diagnosticErrors"] = (
                        ["TRACE_MISSING"] if not matches else
                        ["TRACE_DUPLICATE"] if len(matches) != 1 else
                        _trace_diagnostics(matches[0], request_id=request_id, versions=versions)
                    )
                    if len(matches) == 1:
                        case["terminal"] = _terminal_summary(matches[0].get("terminal"))
                    cases.append(case)
                    continue
                body = response.json()
                if not isinstance(body, dict) or body.get("requestId") != request_id:
                    case["error"] = "PRODUCT_HTTP_RESPONSE_INVALID"
                    cases.append(case)
                    continue
                if body.get("answer") == SYSTEM_FALLBACK_TEXT:
                    case["error"] = "PRODUCT_ANSWER_SERVICE_FAILED"
                    case["diagnosticErrors"] = (
                        ["TRACE_MISSING"] if not matches else
                        ["TRACE_DUPLICATE"] if len(matches) != 1 else
                        _trace_diagnostics(matches[0], request_id=request_id, versions=versions)
                    )
                    if len(matches) == 1:
                        terminal = _terminal_summary(matches[0].get("terminal"))
                        case["terminal"] = terminal
                        if terminal is not None:
                            case["degraded"] = terminal["degraded"]
                    cases.append(case)
                    continue
                if (
                    not isinstance(body.get("answered"), bool)
                    or not isinstance(body.get("answer"), str)
                    or not isinstance(body.get("degraded"), bool)
                    or not isinstance(body.get("sources"), list)
                    or any(not isinstance(source, dict) for source in body["sources"])
                ):
                    case["error"] = "PRODUCT_HTTP_RESPONSE_INVALID"
                    cases.append(case)
                    continue
                if len(matches) != 1:
                    trace = None
                    trace_errors = ["TRACE_MISSING" if not matches else "TRACE_DUPLICATE"]
                else:
                    trace = matches[0]
                    trace_errors = _trace_diagnostics(trace, request_id=request_id, versions=versions)
                case["diagnosticErrors"] = trace_errors
                case["answered"] = body["answered"]
                case["degraded"] = body["degraded"]
                # 正文只进入受控评测产物，供人工逐题核对，不进入常规 trace。
                case["answer"] = body["answer"]
                case["sources"] = body["sources"]
                if trace:
                    reranked_rows = trace.get("reranked", [])
                    fingerprint = trace.get("configFingerprint")
                    fingerprint = fingerprint if isinstance(fingerprint, dict) else {}
                    has_true_rerank = (
                        not body["degraded"]
                        and fingerprint.get("rerankerProvider") != "disabled"
                        and isinstance(reranked_rows, list)
                        and bool(reranked_rows)
                        and all(
                            isinstance(item, dict) and item.get("scoreSource") == "reranker"
                            and type(item.get("rank")) is int and 1 <= item["rank"] <= 100
                            and type(item.get("documentVersion")) is int
                            and isinstance(item.get("documentId"), str)
                            and _finite_score(item.get("score"))
                            for item in reranked_rows
                        )
                    )
                    if has_true_rerank:
                        case["rerankScoreSource"] = "reranker"
                    elif isinstance(reranked_rows, list) and any(
                        isinstance(item, dict) and item.get("scoreSource") == "retrieval_fallback"
                        for item in reranked_rows
                    ):
                        case["rerankScoreSource"] = "retrieval_fallback"
                    else:
                        case["rerankScoreSource"] = "not_run"
                    case["rerankRecords"] = _rerank_records(reranked_rows) if has_true_rerank else []
                    terminal = trace.get("terminal")
                    terminal = terminal if isinstance(terminal, dict) else {}
                    if (
                        terminal.get("outcome") not in ("answered", "refused")
                        or (terminal["outcome"] == "answered") != body["answered"]
                    ):
                        case["diagnosticErrors"].append("TRACE_TERMINAL_MISMATCH")
                    if isinstance(terminal.get("degraded"), bool) and terminal["degraded"] != body["degraded"]:
                        case["diagnosticErrors"].append("TRACE_TERMINAL_MISMATCH")
                    fingerprint = trace.get("configFingerprint")
                    if isinstance(fingerprint, dict):
                        fingerprints.append(fingerprint)
                    for stage, output_stage in (
                        ("retrieved", "retrieved"), ("reranked", "reranked"),
                        ("thresholded", "thresholded"), ("selected", "selected"),
                        ("byteBudget", "finalContext"),
                    ):
                        case[output_stage + "DocumentIds"] = _stage_document_ids(trace.get(stage))
                    if not has_true_rerank:
                        fallback_ids = case.pop("rerankedDocumentIds", ())
                        if case["rerankScoreSource"] == "retrieval_fallback":
                            case["fallbackDocumentIds"] = fallback_ids
                        case["rerankedDocumentIds"] = None
                    citation_ids = tuple(
                        source["documentId"] for source in body["sources"]
                        if isinstance(source, dict) and isinstance(source.get("documentId"), str)
                    )
                    stage_ids = {
                        "retrieval": case.get("retrievedDocumentIds", ()),
                        "reranked": case.get("rerankedDocumentIds", ()),
                        "thresholded": case.get("thresholdedDocumentIds", ()),
                        "selected": case.get("selectedDocumentIds", ()),
                        "finalContext": case.get("finalContextDocumentIds", ()),
                    }
                    stage_sources = {
                        "retrieval": "retrieved", "reranked": "reranked",
                        "thresholded": "thresholded", "selected": "selected",
                        "finalContext": "byteBudget",
                    }
                    for stage, ids in stage_ids.items():
                        if not isinstance(trace.get(stage_sources[stage]), list):
                            continue
                        if stage in {"reranked", "thresholded"} and not has_true_rerank:
                            continue
                        results[stage][entry.id] = EvaluationResult(
                            tuple(ids), body["answered"], citation_ids, case["latencyMs"],
                        )
                    case["terminal"] = _terminal_summary(trace.get("terminal"))
                cases.append(case)
            except httpx.TimeoutException:
                case["latencyMs"] = round((time.perf_counter() - started) * 1000, 3)
                matches = await collect_trace(request_id, wait_seconds=0.5)
                timeout_case = {**case, "error": "PRODUCT_CASE_TIMEOUT"}
                if len(matches) != 1:
                    timeout_case["diagnosticErrors"] = [
                        "TRACE_MISSING" if not matches else "TRACE_DUPLICATE",
                    ]
                else:
                    trace = matches[0]
                    timeout_case["terminal"] = _terminal_summary(trace.get("terminal"))
                    timeout_case["diagnosticErrors"] = _trace_diagnostics(
                        trace, request_id=request_id, versions=versions,
                    )
                cases.append(timeout_case)
            except (httpx.HTTPError, ValueError):
                case["latencyMs"] = round((time.perf_counter() - started) * 1000, 3)
                cases.append({**case, "error": "PRODUCT_HTTP_REQUEST_FAILED"})
    if len(fingerprints) > 1 and any(value != fingerprints[0] for value in fingerprints[1:]):
        for case in cases:
            if "error" not in case:
                case.setdefault("diagnosticErrors", []).append("TRACE_CONFIG_MISMATCH")
    universal_diagnostics = {
        "TRACE_MISSING", "TRACE_DUPLICATE", "TRACE_SCHEMA_INVALID", "TRACE_REQUEST_ID_MISMATCH",
        "TRACE_CONFIG_MISSING", "TRACE_CONFIG_MISMATCH", "TRACE_DURATIONS_MISSING",
        "TRACE_TERMINAL_DATA_MISSING", "TRACE_INCOMPLETE", "TRACE_STAGE_INVALID",
        "TRACE_TERMINAL_MISMATCH", "PRODUCT_INDEX_VERSION_MISMATCH",
    }
    def stage_complete(name: str) -> bool:
        stage_error = f"TRACE_STAGE_MISSING:{dict(retrieval='retrieved', reranked='reranked', thresholded='thresholded', selected='selected', finalContext='byteBudget')[name]}"
        return (
            len(results[name]) == len(dataset.entries)
            and not any(
                row.get("error") or universal_diagnostics.intersection(row.get("diagnosticErrors", []))
                or stage_error in row.get("diagnosticErrors", [])
                for row in cases
            )
        )
    degraded_count = sum(
        bool(row.get("degraded"))
        or (isinstance(row.get("terminal"), dict) and row["terminal"].get("degraded") is True)
        for row in cases
    )
    error_count = sum("error" in row for row in cases)
    fallback_count = sum(row.get("error") == "PRODUCT_ANSWER_SERVICE_FAILED" for row in cases)
    reranker_fallback_count = sum(
        row.get("rerankScoreSource") == "retrieval_fallback" for row in cases
    )
    http_error_count = sum(
        row.get("error") in {
            "PRODUCT_HTTP_REQUEST_FAILED", "PRODUCT_HTTP_RESPONSE_INVALID", "PRODUCT_CASE_TIMEOUT",
        }
        for row in cases
    )
    reranker_disabled = any(
        fingerprint.get("rerankerProvider") == "disabled" for fingerprint in fingerprints
    )
    true_rerank_available = (
        not degraded_count and not reranker_disabled and stage_complete("reranked")
        and all(
            all(
                isinstance(item, dict) and item.get("scoreSource") == "reranker"
                for item in case.get("rerankRecords", [])
            )
            for case in cases if isinstance(case.get("rerankRecords"), list) and case["rerankRecords"]
        )
        and all(isinstance(case.get("rerankRecords"), list) and bool(case["rerankRecords"]) for case in cases if "error" not in case)
    )
    metrics = {}
    for name in results:
        if (
            not stage_complete(name)
            or (name in {"reranked", "thresholded"} and not true_rerank_available)
        ):
            metrics[name] = None
            continue
        metrics[name] = (
            await _retrieval_metrics(dataset, results[name])
            if name == "retrieval"
            else await _candidate_metrics(dataset, results[name])
        )
    return {
        "mode": "http-rag", "runId": run_id, "datasetVersion": dataset.dataset_version,
        "documentVersionFingerprint": versions,
        "configFingerprint": fingerprints[0] if fingerprints and all(
            fingerprint == fingerprints[0] for fingerprint in fingerprints
        ) else None,
        "sampleCount": len(dataset.entries),
        "diagnosticExperiment": len(dataset.entries) != 90,
        "errorCount": error_count,
        "diagnosticErrorCount": sum(len(row.get("diagnosticErrors", [])) for row in cases),
        "fallbackCount": fallback_count,
        "rerankerFallbackCount": reranker_fallback_count,
        "degradedCount": degraded_count,
        "httpErrorCount": http_error_count,
        "trueRerankMetricsAvailable": true_rerank_available,
        "stageMetrics": metrics,
        "answerMetrics": _answer_metrics(dataset, cases),
        "manualAnswerReview": "pending", "cases": cases,
    }


def _coverage_at_3(
    dataset: EvaluationDataset, results: dict[str, EvaluationResult],
) -> float:
    answerable = [entry for entry in dataset.entries if entry.expected_answerable]
    if not answerable:
        return 0.0
    covered = 0
    for entry in answerable:
        expected = set(entry.expected_document_ids)
        top_three = _unique(results[entry.id].retrieved_document_ids, 3)
        if expected.intersection(top_three):
            covered += 1
    return covered / len(answerable)


async def _retrieval_metrics(
    dataset: EvaluationDataset, results: dict[str, EvaluationResult],
) -> dict[str, float]:
    report = await evaluate_customer_service(dataset, lambda entry: results[entry.id])
    return {
        "recall_at_8": report.recall_at_8,
        "mrr_at_3": report.mrr_at_3,
        "ndcg_at_3": report.ndcg_at_3,
    }


def _answer_metrics(dataset: EvaluationDataset, cases: list[dict[str, Any]]) -> dict[str, Any]:
    """保留旧严格引用口径；故障题仍在分母中，但不能冒充正常拒答。"""
    by_id = {case["id"]: case for case in cases}
    answerable = [entry for entry in dataset.entries if entry.expected_answerable]
    no_answer = [entry for entry in dataset.entries if not entry.expected_answerable]
    answered_count = refused_count = citation_count = 0
    for entry in dataset.entries:
        case = by_id.get(entry.id, {})
        if case.get("error"):
            continue
        sources = case.get("sources", [])
        citation_ids = tuple(source.get("documentId") for source in sources)
        if entry.expected_answerable:
            answered_count += case.get("answered") is True
            citation_count += (
                case.get("answered") is True and bool(citation_ids)
                and all(isinstance(value, str) for value in citation_ids)
                and len(citation_ids) == len(set(citation_ids))
                and set(citation_ids).issubset(entry.expected_document_ids)
            )
        else:
            correct_refusal = case.get("answered") is False and not sources
            refused_count += correct_refusal
            citation_count += correct_refusal
    def ratio(count: int, denominator: int) -> float:
        return count / denominator if denominator else 0.0
    return {
        "answerableCount": len(answerable), "answeredCount": answered_count,
        "answerableAnswerRate": ratio(answered_count, len(answerable)),
        "noAnswerCount": len(no_answer), "safeRefusalCount": refused_count,
        "safeRefusalRate": ratio(refused_count, len(no_answer)),
        "strictCitationPassedCount": citation_count,
        "strictCitationValidity": ratio(citation_count, len(dataset.entries)),
        "httpLatencyMs": _latency_summary([
            case["latencyMs"] for case in cases if _finite_score(case.get("latencyMs"))
        ]),
        "errorCount": sum(bool(case.get("error")) for case in cases),
        "degradedCount": sum(
            case.get("degraded") is True
            or isinstance(case.get("terminal"), dict) and case["terminal"].get("degraded") is True
            for case in cases
        ),
    }


async def _candidate_metrics(
    dataset: EvaluationDataset, results: dict[str, EvaluationResult],
) -> dict[str, float]:
    report = await evaluate_customer_service(dataset, lambda entry: results[entry.id])
    return {
        "mrr_at_3": report.mrr_at_3,
        "ndcg_at_3": report.ndcg_at_3,
        "coverage_at_3": _coverage_at_3(dataset, results),
    }


async def run_live(
    dataset: EvaluationDataset, versions: dict[str, int], *, timeout: float,
) -> dict[str, Any]:
    from ai_service.app import create_app
    from ai_service.orchestration.customer_service_rag import CustomerServiceRagService, SYSTEM_FALLBACK_TEXT

    app = create_app()
    cases, raw_results, ranked_results = [], {}, {}
    true_rerank_available = True
    async with app.router.lifespan_context(app):
        state = app.state
        if state.customer_service_rag_service is None:
            raise ValueError("PRODUCT_RAG_UNAVAILABLE")
        for entry in dataset.entries:
            store = _RecordingStore(state.customer_service_knowledge_store, versions)
            reranker = _RecordingReranker(state.reranker)
            service = CustomerServiceRagService(
                state.settings, state.customer_service_embedding_provider, store, reranker, state.model,
            )
            started = time.perf_counter()
            try:
                answer = await asyncio.wait_for(service.answer(entry.question), timeout)
                if store.version_mismatch:
                    raise ValueError("PRODUCT_INDEX_VERSION_MISMATCH")
                if answer.answer == SYSTEM_FALLBACK_TEXT:
                    raise ValueError("PRODUCT_ANSWER_SERVICE_FAILED")
                elapsed = (time.perf_counter() - started) * 1000
                citations = tuple(row.document_id for row in answer.sources)
                raw_results[entry.id] = EvaluationResult(store.retrieved, answer.answered, citations, elapsed)
                # 降级与 disabled 只沿用原顺序，不能伪装成真实重排结果。
                true_rerank = bool(reranker.ranked) and not answer.degraded
                if true_rerank:
                    ranked_results[entry.id] = replace(
                        raw_results[entry.id], retrieved_document_ids=reranker.ranked,
                    )
                else:
                    true_rerank_available = False
                cases.append({
                    "id": entry.id, "answered": answer.answered, "answer": answer.answer,
                    "sources": [asdict(row) for row in answer.sources],
                    "retrievedDocumentIds": store.retrieved,
                    "rerankedDocumentIds": reranker.ranked if true_rerank else None,
                    "rerankScoreSource": "reranker" if true_rerank else "retrieval_fallback",
                    "degraded": answer.degraded, "latencyMs": elapsed,
                })
            except Exception as error:
                # 禁止输出供应商异常正文或令牌；服务失败不算成功拒答。
                code = "PRODUCT_CASE_TIMEOUT" if isinstance(error, TimeoutError) else "PRODUCT_CASE_FAILED"
                if store.version_mismatch:
                    code = "PRODUCT_INDEX_VERSION_MISMATCH"
                cases.append({"id": entry.id, "error": code})
            print(f"已完成 {len(cases)}/90", flush=True)
    errors = sum("error" in row for row in cases)
    retrieval_metrics = (
        None if errors else await _retrieval_metrics(dataset, raw_results)
    )
    reranked_metrics = (
        None if errors or not true_rerank_available
        else await _candidate_metrics(dataset, ranked_results)
    )
    return {
        "mode": "live-rag", "sampleCount": len(cases), "errorCount": errors,
        "degradedCount": sum(row.get("degraded", False) for row in cases),
        "retrievalMetrics": retrieval_metrics,
        "rerankedMetrics": reranked_metrics,
        "manualAnswerReview": "pending", "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="校验产品知识；--live 显式连接真实依赖")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--document-map", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--http-base-url")
    parser.add_argument("--run-id")
    parser.add_argument("--trace-file", type=Path)
    parser.add_argument("--case-ids", nargs="+")
    args = parser.parse_args()
    http_mode = args.http_base_url is not None
    http_only_values = any((args.http_base_url, args.run_id, args.trace_file, args.case_ids))
    if args.live and http_mode:
        parser.error("--live 与 --http-base-url 互斥")
    if args.live and http_only_values:
        parser.error("HTTP 专用参数不能与 --live 同时使用")
    if not args.live and not http_mode and http_only_values:
        parser.error("--run-id、--trace-file 和 --case-ids 仅适用于 HTTP 模式")
    dataset, report = validate_corpus(args.root)
    output_reserved = False
    if args.live or http_mode:
        if args.document_map is None or args.output is None or not 0 < args.timeout <= 300:
            parser.error("真实评测需要 --document-map、--output 和有效的 --timeout")
        if args.output.exists():
            parser.error("输出文件已存在，拒绝覆盖")
        if http_mode and not args.output.resolve().is_relative_to(EVALUATION_OUTPUT_ROOT.resolve()):
            parser.error("含回答正文的 HTTP 评测产物必须保存到仓库已忽略的 tmp/ 目录")
        dataset, versions = map_dataset(dataset, report, _json(args.document_map))
        if http_mode:
            if args.run_id is None or args.trace_file is None:
                parser.error("HTTP 模式需要 --run-id 和 --trace-file")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", args.run_id):
                parser.error("--run-id 仅允许字母、数字、点、下划线和连字符，长度最多 64")
            parsed_url = urlparse(args.http_base_url)
            if not parsed_url.scheme or not parsed_url.netloc:
                parser.error("--http-base-url 必须是绝对 HTTP(S) 地址")
            if parsed_url.scheme not in {"http", "https"}:
                parser.error("--http-base-url 仅支持 HTTP(S)")
            if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
                parser.error("--http-base-url 不得包含凭据、查询参数或片段")
            if args.trace_file.resolve() == args.output.resolve():
                parser.error("--trace-file 与 --output 必须是不同文件")
            if not args.trace_file.is_file():
                parser.error("--trace-file 必须指向服务正在写入的 JSONL 文件")
            all_ids = {entry.id for entry in dataset.entries}
            if args.case_ids:
                if len(args.case_ids) != len(set(args.case_ids)) or not set(args.case_ids) <= all_ids:
                    parser.error("--case-ids 必须是互不重复的有效评测条目 ID")
                dataset = replace(dataset, entries=tuple(
                    entry for entry in dataset.entries if entry.id in set(args.case_ids)
                ))
            settings = Settings()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                parser.error("输出文件已存在，拒绝覆盖")
            os.close(descriptor)
            output_reserved = True
            report = asyncio.run(run_http(
                dataset, versions, base_url=args.http_base_url, run_id=args.run_id,
                trace_file=args.trace_file, token=settings.internal_bearer_token,
                timeout=args.timeout,
            ))
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                parser.error("输出文件已存在，拒绝覆盖")
            os.close(descriptor)
            output_reserved = True
            report = asyncio.run(run_live(dataset, versions, timeout=args.timeout))
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        if args.output.exists() and not output_reserved:
            parser.error("输出文件已存在，拒绝覆盖")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    # HTTP 产物含知识摘录和答案，终端只显示聚合摘要，避免落入通用日志。
    print(json.dumps(
        {key: value for key, value in report.items() if key != "cases"}
        if http_mode else report, ensure_ascii=False, indent=2,
    ))
    if report.get("errorCount", 0) or report.get("diagnosticErrorCount", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
