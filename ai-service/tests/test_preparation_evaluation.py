"""固定公开样本驱动的规则选择回归，不使用真实模型。"""

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_service.prompts.frontend_skills import resolve_skills
from ai_service.models.generation_brief import GenerationBrief
from ai_service.models.input_review import InputReviewError
from ai_service.orchestration.preparation_evaluation import (
    EvaluationDataset, RewriteBudget, evaluate, load_dataset, run_evaluation,
    _source_quote_coverage,
)

DATASET = Path(__file__).resolve().parents[1] / "evaluation/generation-preparation-v1.json"
CASES = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda item: item["id"])
def test_product_selection_respects_public_case_expectations(case):
    selected = resolve_skills(case["branch"], "edit" if case["exists"] else "create", case["prompt"])
    assert case["expectedSkill"] in {item["id"] for item in selected}


@pytest.mark.parametrize("prompt,expected", [
    ("不要后台，但是要制作购物网站", "product-commerce"),
    ("Create a no-code admin panel", "product-dashboard"),
    ("Build an admin panel, not a shopping website", "product-dashboard"),
    ("展示 shader 示例的作品集", "product-content"),
])
def test_selector_handles_contrast_and_english_word_boundaries(prompt, expected):
    assert expected in {item["id"] for item in resolve_skills("HTML", "create", prompt)}


class FakeRewriter:
    def __init__(self, failure=None):
        self.contexts = []
        self.failure = failure

    async def rewrite_requirements(self, context):
        self.contexts.append(deepcopy(context))
        if self.failure:
            raise self.failure
        return GenerationBrief(requirements=[context["prompt"]], preserve_constraints=[],
                               design_brief="PRIVATE_DESIGN", assumptions=[], interactions=[], acceptance=[])


SETTINGS = SimpleNamespace(prompt_rewrite_timeout_seconds=1, prompt_rewrite_max_bytes=8192,
                           prompt_rewrite_max_tokens=1500, model_base_url="https://private.example",
                           model_name="private-model")


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(extra="forbidden"),
    lambda data: data["cases"].append(data["cases"][0]),
    lambda data: data["cases"][0].update(exists="false"),
    lambda data: data["cases"][0].update(requiredQuotes=["not in prompt"]),
    lambda data: data["cases"][0].update(requiredQuotes=["后台", "后台"]),
    lambda data: data["cases"][0].update(id="../unsafe"),
    lambda data: data["cases"][0].update(branch="UNKNOWN"),
    lambda data: data.update(cases=[]),
])
def test_dataset_rejects_invalid_or_coerced_inputs(mutation):
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    mutation(data)
    with pytest.raises(ValueError):
        EvaluationDataset.model_validate(data)


@pytest.mark.asyncio
async def test_offline_all_arms_have_no_rewrite_measurement():
    report = await evaluate(load_dataset(DATASET))
    assert report["rewriteAttempts"] == 0
    assert report["caseCount"] == 12
    assert len(report["rows"]) == 36
    for variant, summary in report["summary"].items():
        assert summary["originalPreserved"] == 12
        assert summary["sourceQuoteCoverage"] is None
        assert summary["sourceQuoteMeasured"] == 0
        assert summary["attemptMedianMs"] is None
        assert summary["selectionMatchRate"] == (None if variant == "baseline" else 1)
    assert all(row["rewriteStatus"] == "not_run" for row in report["rows"] if row["variant"] == "enhanced")


@pytest.mark.asyncio
async def test_real_injected_model_is_called_once_per_enhanced_case_and_report_is_private():
    model = FakeRewriter()
    dataset = load_dataset(DATASET)
    before = dataset.model_dump_json()
    report = await evaluate(dataset, model=model, settings=SETTINGS)
    assert report["rewriteAttempts"] == len(model.contexts) == 6
    assert dataset.model_dump_json() == before
    assert all(len(context["selectedSkills"]) == 4 for context in model.contexts)
    assert report["summary"]["enhanced"]["rewriteReady"] == 6
    assert report["summary"]["enhanced"]["sourceQuoteCoverage"] == 1
    assert report["summary"]["skills_only"]["sourceQuoteCoverage"] is None
    serialized = json.dumps(report, allow_nan=False, ensure_ascii=False)
    for private in ["PRIVATE_DESIGN", SETTINGS.model_base_url, SETTINGS.model_name, *[case.prompt for case in dataset.cases]]:
        assert private not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,reason", [(TimeoutError("PRIVATE"), "TIMEOUT"),
                                             (RuntimeError("PRIVATE"), "PROVIDER_ERROR")])
async def test_fallbacks_count_attempts_but_not_quote_coverage(failure, reason):
    model = FakeRewriter(failure)
    report = await evaluate(load_dataset(DATASET), model=model, settings=SETTINGS, limit=2)
    summary = report["summary"]["enhanced"]
    assert summary["rewriteFallback"] == 2
    assert summary["fallbackReasons"] == {reason: 2}
    assert summary["sourceQuoteCoverage"] is None
    assert summary["attemptP95Ms"] >= summary["attemptMedianMs"] >= 0
    assert "PRIVATE" not in json.dumps(report)


@pytest.mark.asyncio
async def test_budget_failure_is_failed_not_fallback():
    model = FakeRewriter(InputReviewError("INPUT_CONTEXT_BUDGET_EXCEEDED", "PRIVATE"))
    report = await evaluate(load_dataset(DATASET), model=model, settings=SETTINGS, limit=1)
    summary = report["summary"]["enhanced"]
    assert summary["rewriteFailed"] == 1
    assert summary["rewriteFallback"] == summary["rewriteAttempts"] == 0
    assert report["rewriteAttempts"] == 1  # 调用额度在委托前消耗，实际 HTTP 未发出。


@pytest.mark.asyncio
async def test_call_cap_includes_failures_and_blocks_before_delegation():
    model = FakeRewriter(RuntimeError("PRIVATE"))
    budget = RewriteBudget(model, 1)
    for _ in range(2):
        with pytest.raises(RuntimeError):
            await budget.rewrite_requirements({})
    assert len(model.contexts) == 1
    with pytest.raises(ValueError, match="EVALUATION_INVALID_LIMIT"):
        await evaluate(load_dataset(DATASET), model=model, settings=SETTINGS, limit=7)
    assert len(model.contexts) == 1


@pytest.mark.asyncio
async def test_file_collision_and_invalid_limit_precede_model_construction(tmp_path, monkeypatch):
    from ai_service.models import openai_compatible
    monkeypatch.setattr(openai_compatible, "OpenAICompatibleModel", lambda _: pytest.fail("不应创建模型"))
    output = tmp_path / "report.json"
    output.write_text("existing", encoding="utf-8")
    with pytest.raises(FileExistsError):
        await run_evaluation(DATASET, output, real_model=True)
    with pytest.raises(ValueError, match="EVALUATION_INVALID_LIMIT"):
        await run_evaluation(DATASET, tmp_path / "new.json", real_model=True, limit=7)
    assert output.read_text() == "existing"


@pytest.mark.asyncio
async def test_offline_cli_path_never_loads_settings_or_model(tmp_path, monkeypatch):
    from ai_service import config
    from ai_service.models import openai_compatible
    monkeypatch.setattr(config, "Settings", lambda **_: pytest.fail("离线不读取配置"))
    monkeypatch.setattr(openai_compatible, "OpenAICompatibleModel", lambda _: pytest.fail("离线不创建模型"))
    output = tmp_path / "report.json"
    report = await run_evaluation(DATASET, output)
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_coverage_only_credits_grounded_excerpts_not_design_or_descriptions():
    brief = {"requirements": ["保留图片"], "preserve_constraints": [], "design_brief": "禁止付款",
             "interactions": [{"description": "禁止付款", "evidence": "保留图片"}],
             "acceptance": [], "assumptions": ["禁止付款"]}
    assert _source_quote_coverage(brief, ["保留图片", "禁止付款"]) == .5
    assert _source_quote_coverage(None, ["禁止付款"]) is None
