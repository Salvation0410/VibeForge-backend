"""验证产品集的来源、拒答标签和真实文档映射，避免产生虚假的检索成绩。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ai_service.orchestration.product_knowledge_evaluation import (
    DEFAULT_ROOT, QUOTAS, map_dataset, validate_corpus,
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
