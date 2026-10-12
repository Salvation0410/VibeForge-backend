"""产品知识校验与真实 RAG 评测入口；默认只解析本地公共知识正文。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ai_service.config import Settings
from ai_service.orchestration.customer_service_evaluation import (
    EvaluationDataset, EvaluationResult, evaluate_customer_service, load_evaluation_dataset,
)
from ai_service.orchestration.document_etl import parse_document, split_sections


DEFAULT_ROOT = Path(__file__).resolve().parents[4] / "doc/knowledge/product"
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


async def run_live(
    dataset: EvaluationDataset, versions: dict[str, int], *, timeout: float,
) -> dict[str, Any]:
    from ai_service.app import create_app
    from ai_service.orchestration.customer_service_rag import CustomerServiceRagService, SYSTEM_FALLBACK_TEXT

    app = create_app()
    cases, raw_results, ranked_results = [], {}, {}
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
                # 重排失败降级时遵循原始候选顺序；独立报告降级数量，不掩盖故障。
                ranked = reranker.ranked or store.retrieved[:state.settings.rag_final_top_k]
                ranked_results[entry.id] = replace(raw_results[entry.id], retrieved_document_ids=ranked)
                cases.append({
                    "id": entry.id, "answered": answer.answered, "answer": answer.answer,
                    "sources": [asdict(row) for row in answer.sources],
                    "retrievedDocumentIds": store.retrieved, "rerankedDocumentIds": ranked,
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
    return {
        "mode": "live-rag", "sampleCount": len(cases), "errorCount": errors,
        "degradedCount": sum(row.get("degraded", False) for row in cases),
        "retrievalMetrics": None if errors else asdict(await evaluate_customer_service(dataset, lambda e: raw_results[e.id])),
        "rerankedMetrics": None if errors else asdict(await evaluate_customer_service(dataset, lambda e: ranked_results[e.id])),
        "manualAnswerReview": "pending", "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="校验产品知识；--live 显式连接真实依赖")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--document-map", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    dataset, report = validate_corpus(args.root)
    if args.live:
        if args.document_map is None or args.output is None or not 0 < args.timeout <= 300:
            parser.error("真实评测需要 --document-map、--output 和有效的 --timeout")
        dataset, versions = map_dataset(dataset, report, _json(args.document_map))
        report = asyncio.run(run_live(dataset, versions, timeout=args.timeout))
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    if report.get("errorCount", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
