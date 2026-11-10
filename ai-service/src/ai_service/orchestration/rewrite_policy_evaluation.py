"""无模型调用的策略分类评估；预测省去调用不等于已验证应用质量或真实耗时。"""

import argparse
from collections import Counter
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_service.models.rewrite_policy import POLICY_VERSION, should_skip_rewrite


class PolicyCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    branch: Literal["HTML", "MULTI_FILE", "VUE_PROJECT"]
    prompt: str = Field(min_length=1, max_length=4000)
    exists: bool
    expectedSkip: bool

    @model_validator(mode="after")
    def check_case(self):
        if not self.prompt.strip() or (self.expectedSkip and not self.exists):
            raise ValueError("POLICY_CASE_INVALID")
        return self


class PolicyDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schemaVersion: Literal["rewrite-policy-eval/v1"]
    datasetVersion: str = Field(pattern=r"^[a-zA-Z0-9.-]{1,64}$")
    split: Literal["development", "holdout"]
    cases: list[PolicyCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("POLICY_DUPLICATE_ID")
        return self


def load_dataset(path: Path) -> PolicyDataset:
    if path.stat().st_size > 2_000_000:
        raise ValueError("POLICY_DATASET_TOO_LARGE")
    return PolicyDataset.model_validate_json(path.read_text(encoding="utf-8"))


def _metrics(rows):
    counts = Counter((row["expectedSkip"], row["predictedSkip"]) for row in rows)
    tp, fp, fn, tn = (counts[key] for key in [(True, True), (False, True), (True, False), (False, False)])
    return {"cases": len(rows), "correctSkip": tp, "falseSkip": fp, "missedSkip": fn, "correctRewrite": tn,
            "skipPrecision": tp / (tp + fp) if tp + fp else None,
            "skipRecall": tp / (tp + fn) if tp + fn else None,
            "falseSkipRate": fp / (fp + tn) if fp + tn else None,
            "alwaysPlannedCalls": len(rows), "conservativePlannedCalls": len(rows) - tp - fp,
            "predictedAvoidedCalls": tp + fp}


def evaluate(dataset: PolicyDataset):
    rows = [{"id": case.id, "branch": case.branch, "mode": "edit" if case.exists else "create",
             "expectedSkip": case.expectedSkip,
             "predictedSkip": should_skip_rewrite({"prompt": case.prompt, "currentArtifact": {"exists": case.exists}})}
            for case in dataset.cases]
    # 只保留固定枚举、样本 ID 和布尔标签；需求不写入报告，哈希不用于恢复原文。
    return {"schemaVersion": "rewrite-policy-report/v1", "createdAt": datetime.now(UTC).isoformat(),
            "datasetVersion": dataset.datasetVersion, "split": dataset.split,
            "datasetSha256": hashlib.sha256(dataset.model_dump_json().encode()).hexdigest(),
            "policyVersion": POLICY_VERSION, "scope": "policy_classification_only", "actualModelCalls": 0,
            "summary": _metrics(rows),
            "byBranch": {branch: _metrics([row for row in rows if row["branch"] == branch])
                         for branch in ("HTML", "MULTI_FILE", "VUE_PROJECT")},
            "byMode": {mode: _metrics([row for row in rows if row["mode"] == mode]) for mode in ("create", "edit")},
            "rows": rows}


def run_evaluation(dataset_path: Path, output: Path):
    dataset = load_dataset(dataset_path)
    with output.open("x", encoding="utf-8") as handle:
        report = evaluate(dataset)
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return report


def main():
    parser = argparse.ArgumentParser(description="纯离线重写策略评估，不调用模型。")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_evaluation(args.dataset, args.output)
    except Exception:
        parser.exit(1, "策略评估未完成：请检查样本格式和输出路径（文件必须尚不存在）。\n")
    print(json.dumps({"split": report["split"], "actualModelCalls": 0, "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
