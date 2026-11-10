"""仅评估准备阶段的离线/真实对照工具；不创建应用，不调用项目工具网关。"""

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median
from types import SimpleNamespace
from time import monotonic
from typing import Annotated, Literal

from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_service.orchestration.events import EventEmitter
from ai_service.models.input_review import InputReviewError
from ai_service.orchestration.nodes.preparation import create_preparation_node
from ai_service.prompts.frontend_skills import RULES, SELECTION_VERSION

MAX_REAL_CALLS = 6
VARIANTS = ("baseline", "skills_only", "enhanced")


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    branch: Literal["HTML", "MULTI_FILE", "VUE_PROJECT"]
    prompt: str = Field(min_length=1, max_length=4000)
    exists: bool
    expectedSkill: Literal["product-dashboard", "product-commerce", "product-content", "preserve-style"]
    requiredQuotes: list[Annotated[str, Field(min_length=1, max_length=400)]] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def validate_quotes(self):
        if (not self.prompt.strip() or len(set(self.requiredQuotes)) != len(self.requiredQuotes)
                or any(not quote.strip() or quote not in self.prompt for quote in self.requiredQuotes)):
            raise ValueError("EVALUATION_INVALID_QUOTES")
        return self


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schemaVersion: Literal["generation-preparation-eval/v1"]
    datasetVersion: str = Field(pattern=r"^[a-zA-Z0-9.-]{1,64}$")
    cases: list[EvaluationCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("EVALUATION_DUPLICATE_ID")
        return self


def load_dataset(path: Path) -> EvaluationDataset:
    # 在 JSON 解析前限制文件体积，防止误选不受限的业务导出文件。
    if path.stat().st_size > 2_000_000:
        raise ValueError("EVALUATION_DATASET_TOO_LARGE")
    return EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))


class RewriteBudget:
    """所有尝试（包括失败）都消耗额度，且只暴露重写方法。"""
    def __init__(self, model, limit):
        self.model, self.limit, self.attempts = model, limit, 0

    async def rewrite_requirements(self, context):
        if self.attempts >= self.limit:
            raise RuntimeError("EVALUATION_CALL_LIMIT")
        self.attempts += 1
        return await self.model.rewrite_requirements(context)


def _source_quote_coverage(brief, quotes):
    if not brief:
        return None
    excerpts = [*brief["requirements"], *brief["preserve_constraints"],
                *(item["evidence"] for item in [*brief["interactions"], *brief["acceptance"]])]
    return sum(any(quote in excerpt for excerpt in excerpts) for quote in quotes) / len(quotes)


def summarize(rows):
    result = {}
    for variant in VARIANTS:
        group = [row for row in rows if row["variant"] == variant]
        matched = [row["selectionMatched"] for row in group if row["selectionMatched"] is not None]
        coverage = [row["sourceQuoteCoverage"] for row in group if row["sourceQuoteCoverage"] is not None]
        # 离线 enhanced 的重写没有执行，不应进入重写耗时或成功率分母。
        attempted = [row for row in group if row["modelAttempted"]]
        durations = sorted(row["durationMs"] for row in attempted)
        result[variant] = {
            "cases": len(group), "originalPreserved": sum(row["originalPreserved"] for row in group),
            "selectionMatched": sum(matched), "selectionMeasured": len(matched),
            "selectionMatchRate": mean(matched) if matched else None,
            "rewriteAttempts": len(attempted),
            "rewriteReady": sum(row["rewriteStatus"] == "ready" for row in attempted),
            "rewriteFallback": sum(row["rewriteStatus"] == "fallback" for row in attempted),
            "rewriteReadyRate": mean(row["rewriteStatus"] == "ready" for row in attempted) if attempted else None,
            "rewriteFallbackRate": mean(row["rewriteStatus"] == "fallback" for row in attempted) if attempted else None,
            "rewriteFailed": sum(row["rewriteStatus"] == "failed" for row in group),
            "failureReasons": dict(Counter(row["reason"] for row in group if row["rewriteStatus"] == "failed")),
            "fallbackReasons": dict(Counter(row["reason"] for row in attempted if row["rewriteStatus"] == "fallback")),
            "preparationMedianMs": median([row["durationMs"] for row in group]) if group else None,
            "attemptMedianMs": median(durations) if durations else None,
            "attemptP95Ms": durations[math.ceil(len(durations) * .95) - 1] if durations else None,
            "sourceQuoteCoverage": mean(coverage) if coverage else None,
            "sourceQuoteMeasured": len(coverage),
        }
    return result


async def evaluate(dataset: EvaluationDataset, *, model=None, settings=None, limit=None):
    """注入模型用于测试；离线模式不构造 Settings、不读取 .env、不创建模型。"""
    real = model is not None
    limit = limit if limit is not None else (MAX_REAL_CALLS if real else len(dataset.cases))
    if type(limit) is not int or limit < 1 or (real and limit > MAX_REAL_CALLS):
        raise ValueError("EVALUATION_INVALID_LIMIT")
    if real and settings is None:
        raise ValueError("EVALUATION_SETTINGS_REQUIRED")
    budget = RewriteBudget(model, min(limit, MAX_REAL_CALLS))
    rows = []
    for case in dataset.cases[:limit]:
        original = {"prompt": case.prompt, "codeGenType": case.branch,
                    "currentArtifact": {"exists": case.exists}, "conversation": [], "metadata": {}}
        for variant in VARIANTS:
            options = SimpleNamespace(
                frontend_skills_enabled=variant != "baseline",
                prompt_rewrite_enabled=real and variant == "enhanced",
                preparation_diagnostics_enabled=True,
                prompt_rewrite_timeout_seconds=settings.prompt_rewrite_timeout_seconds if real else 15,
                prompt_rewrite_max_bytes=settings.prompt_rewrite_max_bytes if real else 8192,
            )
            workflow = SimpleNamespace(settings=options, model=budget, tracer=None,
                                       _raise_if_cancelled=lambda _: None)
            node = create_preparation_node(workflow, EventEmitter("evaluation"), "evaluation")
            # 每组都使用独立输入，避免上一组缓存导致增强未执行。
            started = monotonic()
            try:
                with tracing_context(enabled=False):
                    update = await node({"context": deepcopy(original), "prompt": case.prompt,
                                         "code_gen_type": case.branch})
            except InputReviewError as exc:
                # 硬预算拒绝发生在发送前；保留失败样本，不能计为正常降级或继续生成。
                rows.append({"id": case.id, "branch": case.branch,
                             "mode": "edit" if case.exists else "create", "variant": variant,
                             "rewriteStatus": "failed", "reason": "BUDGET_EXCEEDED"
                             if exc.code == "INPUT_CONTEXT_BUDGET_EXCEEDED" else "INPUT_ERROR",
                             "modelAttempted": False, "durationMs": (monotonic() - started) * 1000,
                             "briefBytes": 0, "skillIds": [], "originalPreserved": True,
                             "selectionMatched": None, "sourceQuoteCoverage": None})
                continue
            context = update["context"]
            diagnostic = update["preparation_diagnostics"]
            prepared = context.get("generationPreparation", {})
            rows.append({
                "id": case.id, "branch": case.branch, "mode": "edit" if case.exists else "create",
                "variant": variant, **diagnostic,
                "rewriteStatus": "not_run" if not real and variant == "enhanced" else diagnostic["rewriteStatus"],
                "originalPreserved": {key: value for key, value in context.items() if key != "generationPreparation"} == original,
                "selectionMatched": None if variant == "baseline" else any(
                    item["id"] == case.expectedSkill for item in prepared.get("skills", [])),
                "sourceQuoteCoverage": _source_quote_coverage(prepared.get("brief"), case.requiredQuotes),
            })
    canonical = dataset.model_dump_json().encode("utf-8")
    return {
        "schemaVersion": "generation-preparation-report/v1", "datasetVersion": dataset.datasetVersion,
        "createdAt": datetime.now(UTC).isoformat(),
        "datasetSha256": hashlib.sha256(canonical).hexdigest(), "selectorVersion": SELECTION_VERSION,
        "ruleVersions": {key: rule.version for key, rule in RULES.items()},
        "scope": "preparation_only", "mode": "real" if real else "offline",
        "caseCount": len(dataset.cases[:limit]), "rewriteAttempts": budget.attempts,
        "configuration": {"timeoutSeconds": settings.prompt_rewrite_timeout_seconds,
                          "maxTokens": settings.prompt_rewrite_max_tokens,
                          "maxBytes": settings.prompt_rewrite_max_bytes,
                          "modelFingerprint": hashlib.sha256(
                              (settings.model_base_url + "|" + settings.model_name).encode()).hexdigest()} if real else None,
        "summary": summarize(rows), "rows": rows,
    }


async def run_evaluation(dataset_path: Path, output: Path, *, real_model=False, limit=None):
    dataset = load_dataset(dataset_path)
    # 参数和目标文件必须先检查；禁止已经花费调用后才发现报告不可写。
    if limit is not None and (type(limit) is not int or limit < 1 or (real_model and limit > MAX_REAL_CALLS)):
        raise ValueError("EVALUATION_INVALID_LIMIT")
    model = None
    with output.open("x", encoding="utf-8") as handle:
        try:
            if real_model:
                from ai_service.config import Settings
                from ai_service.models.openai_compatible import OpenAICompatibleModel
                settings = Settings(langsmith_tracing=False)
                model = OpenAICompatibleModel(settings)
            else:
                settings = None
            report = await evaluate(dataset, model=model, settings=settings, limit=limit)
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            return report
        finally:
            if model is not None:
                # 只关闭本次工具独占的根客户端，重写调用共享连接不在单次调用中关闭。
                try:
                    await model._client.root_async_client.close()
                finally:
                    model._client.root_client.close()


def main():
    parser = argparse.ArgumentParser(description="准备阶段三组评估；默认离线，真实模式最多六次重写。")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--real-model", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    try:
        report = asyncio.run(run_evaluation(args.dataset, args.output, real_model=args.real_model, limit=args.limit))
    except Exception:
        # Pydantic 和供应商异常都可能包含原文；CLI 不输出异常对象或 traceback。
        parser.exit(1, "评估未完成：请检查数据格式、调用上限、配置和输出路径（必须尚不存在）。\n")
    print(json.dumps({"mode": report["mode"], "caseCount": report["caseCount"],
                      "rewriteAttempts": report["rewriteAttempts"], "summary": report["summary"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
