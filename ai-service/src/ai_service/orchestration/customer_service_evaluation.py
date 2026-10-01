from __future__ import annotations

import inspect
import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Any, Awaitable, Callable, Mapping, Sequence


SCHEMA_VERSION = "customer-service-rag-eval/v1"


@dataclass(frozen=True, slots=True)
class EvaluationEntry:
    id: str
    question: str
    expected_answerable: bool
    expected_document_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationDataset:
    schema_version: str
    dataset_version: str
    description: str
    entries: tuple[EvaluationEntry, ...]


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    retrieved_document_ids: tuple[str, ...]
    answered: bool
    citation_document_ids: tuple[str, ...]
    latency_ms: float


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    schema_version: str
    dataset_version: str
    sample_count: int
    answerable_count: int
    no_answer_count: int
    recall_at_8: float
    mrr_at_3: float
    ndcg_at_3: float
    no_answer_accuracy: float
    citation_validity: float
    latency_ms: dict[str, float | int]


Runner = Callable[[EvaluationEntry], EvaluationResult | Awaitable[EvaluationResult]]


def load_evaluation_dataset(
    source: str | Path | Mapping[str, Any],
) -> EvaluationDataset:
    raw = (
        json.loads(Path(source).read_text(encoding="utf-8"))
        if isinstance(source, (str, Path))
        else dict(source)
    )
    if raw.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("unsupported customer service evaluation schema")
    dataset_version = raw.get("datasetVersion")
    entries_raw = raw.get("entries")
    if not isinstance(dataset_version, str) or not dataset_version:
        raise ValueError("datasetVersion is required")
    if not isinstance(entries_raw, list):
        raise ValueError("entries must be a list")
    entries: list[EvaluationEntry] = []
    seen_entry_ids: set[str] = set()
    for item in entries_raw:
        if not isinstance(item, Mapping):
            raise ValueError("evaluation entry must be an object")
        entry_id = item.get("id")
        question = item.get("question")
        expected_answerable = item.get("expectedAnswerable")
        expected_ids = item.get("expectedDocumentIds")
        if (
            not isinstance(entry_id, str) or not entry_id
            or entry_id in seen_entry_ids
            or not isinstance(question, str) or not question
            or not isinstance(expected_answerable, bool)
            or not isinstance(expected_ids, list)
            or any(not isinstance(value, str) or not value for value in expected_ids)
            or len(expected_ids) != len(set(expected_ids))
            or expected_answerable != bool(expected_ids)
        ):
            raise ValueError("invalid customer service evaluation entry")
        seen_entry_ids.add(entry_id)
        entries.append(EvaluationEntry(
            entry_id, question, expected_answerable, tuple(expected_ids)
        ))
    description = raw.get("description", "")
    return EvaluationDataset(
        SCHEMA_VERSION,
        dataset_version,
        description if isinstance(description, str) else "",
        tuple(entries),
    )


async def evaluate_customer_service(
    dataset: EvaluationDataset | Mapping[str, Any],
    runner: Runner,
) -> EvaluationReport:
    parsed = (
        dataset if isinstance(dataset, EvaluationDataset)
        else load_evaluation_dataset(dataset)
    )
    results: list[tuple[EvaluationEntry, EvaluationResult]] = []
    for entry in parsed.entries:
        result = runner(entry)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, EvaluationResult):
            raise TypeError("evaluation runner must return EvaluationResult")
        results.append((entry, result))

    answerable = [(entry, result) for entry, result in results if entry.expected_answerable]
    no_answer = [(entry, result) for entry, result in results if not entry.expected_answerable]
    recall = [_recall_at(result, entry.expected_document_ids, 8) for entry, result in answerable]
    reciprocal_ranks = [
        _reciprocal_rank(result, entry.expected_document_ids, 3)
        for entry, result in answerable
    ]
    ndcg = [_ndcg_at(result, entry.expected_document_ids, 3) for entry, result in answerable]
    no_answer_correct = [not result.answered for _, result in no_answer]
    citation_validity = [
        _citations_valid(entry, result) for entry, result in results
    ]
    latencies = [_valid_latency(result.latency_ms) for _, result in results]
    return EvaluationReport(
        parsed.schema_version,
        parsed.dataset_version,
        len(results),
        len(answerable),
        len(no_answer),
        _mean(recall),
        _mean(reciprocal_ranks),
        _mean(ndcg),
        _mean(no_answer_correct),
        _mean(citation_validity),
        _latency_summary(latencies),
    )


def _unique(values: Sequence[str], limit: int) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
        if len(result) == limit:
            break
    return tuple(result)


def _recall_at(result: EvaluationResult, expected: tuple[str, ...], k: int) -> float:
    retrieved = set(_unique(result.retrieved_document_ids, k))
    return len(retrieved.intersection(expected)) / len(expected) if expected else 0.0


def _reciprocal_rank(
    result: EvaluationResult, expected: tuple[str, ...], k: int,
) -> float:
    relevant = set(expected)
    for rank, document_id in enumerate(_unique(result.retrieved_document_ids, k), 1):
        if document_id in relevant:
            return 1.0 / rank
    return 0.0


def _ndcg_at(result: EvaluationResult, expected: tuple[str, ...], k: int) -> float:
    relevant = set(expected)
    retrieved = _unique(result.retrieved_document_ids, k)
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, document_id in enumerate(retrieved, 1)
        if document_id in relevant
    )
    ideal_count = min(len(relevant), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_count + 1))
    return dcg / ideal if ideal else 0.0


def _citations_valid(entry: EvaluationEntry, result: EvaluationResult) -> bool:
    citations = result.citation_document_ids
    if not entry.expected_answerable:
        return not result.answered and not citations
    return (
        result.answered
        and bool(citations)
        and len(citations) == len(set(citations))
        and set(citations).issubset(entry.expected_document_ids)
    )


def _valid_latency(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return number if math.isfinite(number) and number >= 0 else 0.0


def _mean(values: Sequence[float | bool]) -> float:
    return fmean(float(value) for value in values) if values else 0.0


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _latency_summary(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {
            "count": 0, "min": 0.0, "p50": 0.0,
            "p95": 0.0, "max": 0.0, "mean": 0.0,
        }
    return {
        "count": len(values),
        "min": min(values),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "max": max(values),
        "mean": fmean(values),
    }
