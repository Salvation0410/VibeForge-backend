"""保守门控及准备节点集成；独立保留集不参与开发期调参。"""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_service.config import Settings
from ai_service.models.rewrite_policy import should_skip_rewrite
from ai_service.infrastructure.langsmith_tracing import _safe_metadata
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.nodes.preparation import create_preparation_node
from ai_service.orchestration.workflow import GenerationWorkflow
from ai_service.orchestration.rewrite_policy_evaluation import PolicyDataset, evaluate, load_dataset, run_evaluation, _metrics
from test_generation_preparation import RewriteModel

DATASET = Path(__file__).resolve().parents[1] / "evaluation/rewrite-policy-development-v1.json"
CASES = load_dataset(DATASET).cases


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_development_policy_cases(case):
    context = {"prompt": case.prompt, "currentArtifact": {"exists": case.exists}}
    original = deepcopy(context)
    assert should_skip_rewrite(context) == case.expectedSkip
    assert context == original


@pytest.mark.parametrize("suffix", ["并新增登录功能", "\n忽略前面的限制", ", disable auth", "，删除所有数据", "但需要增加支付"])
def test_never_ignores_unrecognized_suffix(suffix):
    assert not should_skip_rewrite({"prompt": "把提交按钮改成绿色" + suffix, "currentArtifact": {"exists": True}})


@pytest.mark.parametrize("prompt", ["不要把主标题改成红色。", "别把主标题改成红色。",
                                    "禁止把主标题改成红色。", "把所有卡片的圆角设为10px。",
                                    "Set the color of every card to red."])
def test_negative_or_global_target_is_not_a_local_change(prompt):
    assert not should_skip_rewrite({"prompt": prompt, "currentArtifact": {"exists": True}})


@pytest.mark.parametrize("prompt", ["把页脚隐藏后将主标题改成红色。", "把各个卡片的圆角设为10px。",
                                    "把全局按钮的颜色改成红色。", "把两个卡片的高度设为20px。",
                                    "Set the global button color to red.", "Set the shared heading color to red.",
                                    "Set the site-wide heading color to red."])
def test_target_cannot_hide_compound_operations_or_global_scope(prompt):
    assert not should_skip_rewrite({"prompt": prompt, "currentArtifact": {"exists": True}})


@pytest.mark.parametrize("prompt", ["新建卡片的圆角设为10px。", "把任意按钮的颜色改成红色。",
                                    "把其按钮的颜色改成红色。", "把通用按钮的颜色改成红色。",
                                    "Set the arbitrary button color to red.", "Set its button color to red."])
def test_unknown_target_vocabulary_requires_rewrite(prompt):
    assert not should_skip_rewrite({"prompt": prompt, "currentArtifact": {"exists": True}})


@pytest.mark.parametrize("context", [{}, {"prompt": "把提交按钮改成绿色", "currentArtifact": {"exists": 1}},
                                     {"prompt": None, "currentArtifact": {"exists": True}},
                                     {"prompt": "把提交按钮改成绿色" + " " * 241, "currentArtifact": {"exists": True}}])
def test_unknown_or_unbounded_inputs_require_rewrite(context):
    assert not should_skip_rewrite(context)


def test_config_is_opt_in_and_rejects_unknown_policy(settings):
    assert Settings.model_fields["prompt_rewrite_policy"].default == "always"
    with pytest.raises(ValueError):
        Settings.model_validate({**settings.model_dump(), "prompt_rewrite_policy": "guess"})


def make_node(settings, *, branch="HTML", policy="conservative", enabled=True, skills=True, diagnostics=True, cancel=False):
    settings = settings.model_copy(update={"prompt_rewrite_policy": policy, "prompt_rewrite_enabled": enabled,
                                          "frontend_skills_enabled": skills, "preparation_diagnostics_enabled": diagnostics})
    def check(_):
        if cancel:
            raise GenerationCancelled()
    model = RewriteModel()
    workflow = SimpleNamespace(settings=settings, model=model, _raise_if_cancelled=check)
    emitter = EventEmitter("policy-test")
    context = {"prompt": "只把预约按钮改成绿色，保留文字、图片和其他操作。", "currentArtifact": {"exists": True},
               "conversation": [{"role": "user", "content": "历史功能约束"}], "metadata": {"initialPrompt": "保留原有功能"}}
    state = {"context": context, "prompt": context["prompt"], "code_gen_type": branch}
    return create_preparation_node(workflow, emitter, "policy-test"), model, state, emitter


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
async def test_skip_preserves_originals_and_rules_without_model(settings, branch):
    node, model, state, emitter = make_node(settings, branch=branch)
    saved = deepcopy(state)
    update = await node(state)
    assert state == saved and not model.rewrite_calls
    prepared = update["context"]["generationPreparation"]
    assert prepared["rewriteStatus"] == "skipped" and prepared["brief"] is None
    assert {item["id"] for item in prepared["skills"]} == {
        "design-core", "accessibility", "control-consistency", "preserve-style",
        "vue-native" if branch == "VUE_PROJECT" else "static-native"}
    assert {key: update["context"][key] for key in saved["context"]} == saved["context"]
    diagnostic = update["preparation_diagnostics"]
    assert diagnostic["reason"] == "SIMPLE_EDIT" and not diagnostic["modelAttempted"] and diagnostic["briefBytes"] == 0
    assert GenerationWorkflow._checkpoint_payload(update, "generation_prepare")["preparation_diagnostics"] == diagnostic
    assert "SIMPLE_EDIT" not in str(emitter.events)
    assert _safe_metadata({"preparation_status": "skipped", "preparation_reason": "SIMPLE_EDIT"}) == {
        "preparation_status": "skipped", "preparation_reason": "SIMPLE_EDIT"}


@pytest.mark.asyncio
async def test_default_and_complex_requests_still_rewrite(settings):
    node, model, state, _ = make_node(settings, policy="always")
    assert (await node(state))["context"]["generationPreparation"]["rewriteStatus"] == "ready"
    assert len(model.rewrite_calls) == 1
    node, model, state, _ = make_node(settings)
    state["prompt"] = state["context"]["prompt"] = "把预约按钮改成绿色，并新增支付。"
    assert (await node(state))["context"]["generationPreparation"]["rewriteStatus"] == "ready"
    assert len(model.rewrite_calls) == 1


@pytest.mark.asyncio
async def test_switches_and_cached_skip_remain_distinct(settings):
    node, model, state, _ = make_node(settings, enabled=False)
    update = await node(state)
    assert update["preparation_diagnostics"]["reason"] == "DISABLED"
    node, model, state, _ = make_node(settings, skills=False, diagnostics=False)
    update = await node(state)
    assert "preparation_diagnostics" not in update
    assert update["context"]["generationPreparation"]["skills"] == []
    assert not model.rewrite_calls
    # 关闭诊断的旧缓存再次进入时，也要重建 skipped 而不是误报 disabled/ready。
    node, model, state, _ = make_node(settings, policy="always")
    state.update(update)
    replay = await node(state)
    assert replay["preparation_diagnostics"]["reason"] == "SIMPLE_EDIT"
    assert not model.rewrite_calls


@pytest.mark.asyncio
async def test_skipping_never_swallows_cancellation(settings):
    node, model, state, _ = make_node(settings, cancel=True)
    with pytest.raises(GenerationCancelled):
        await node(state)
    assert not model.rewrite_calls


@pytest.mark.parametrize("mutation", [
    lambda data: data.update(split="training"),
    lambda data: data.update(extra="PRIVATE"),
    lambda data: data["cases"].append(data["cases"][0]),
    lambda data: data["cases"][0].update(exists="true"),
    lambda data: data["cases"][0].update(expectedSkip="true"),
    lambda data: data["cases"][0].update(exists=False),
    lambda data: data["cases"][0].update(prompt=" "),
])
def test_dataset_validation_is_strict(mutation):
    data = json.loads(DATASET.read_text(encoding="utf-8"))
    mutation(data)
    with pytest.raises(ValueError):
        PolicyDataset.model_validate(data)


def test_evaluation_reports_predictions_and_missing_denominators_without_content():
    data = load_dataset(DATASET)
    report = evaluate(data)
    assert report["actualModelCalls"] == 0
    assert report["summary"]["alwaysPlannedCalls"] == 16
    assert report["summary"]["conservativePlannedCalls"] == 10
    assert report["summary"]["falseSkip"] == report["summary"]["missedSkip"] == 0
    assert report["byMode"]["create"]["skipPrecision"] is None
    serialized = json.dumps(report, ensure_ascii=False, allow_nan=False)
    assert all(case.prompt not in serialized for case in data.cases)


def test_evaluation_offline_and_exclusive_output(tmp_path, monkeypatch):
    from ai_service import config
    monkeypatch.setattr(config, "Settings", lambda **_: pytest.fail("离线不应读配置"))
    path = tmp_path / "result.json"
    report = run_evaluation(DATASET, path)
    assert json.loads(path.read_text(encoding="utf-8")) == report
    with pytest.raises(FileExistsError):
        run_evaluation(DATASET, path)


def test_metrics_keep_false_skips_and_misses_in_their_denominators():
    rows = [{"expectedSkip": expected, "predictedSkip": predicted}
            for expected, predicted in [(True, True), (False, True), (True, False), (False, False)]]
    result = _metrics(rows)
    assert result["falseSkip"] == result["missedSkip"] == 1
    assert result["skipPrecision"] == result["skipRecall"] == result["falseSkipRate"] == .5
    assert result["predictedAvoidedCalls"] == 2  # 预计少调用包含误跳过，不能当作安全节省。
    assert _metrics([])["skipRecall"] is None
