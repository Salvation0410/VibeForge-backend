"""审查只使用当前证据；这些离线断言不证明模型能判断组件语义。"""

from copy import deepcopy
import json

import pytest

from ai_service.config import Settings
from ai_service.models.input_review import InputReviewError
from test_openai_compatible import model_with_response
from ai_service.prompts.review import VUE_REVIEW_EVIDENCE_INSTRUCTIONS, quality_review_system_prompt


async def test_vue_review_discards_generation_history_without_losing_edit_constraints():
    context = {
        "codeGenType": "VUE_PROJECT", "prompt": "只调整标题颜色",
        "conversation": [{"role": "user", "content": "保留筛选"},
                         {"role": "assistant", "content": "STALE_ASSISTANT"}],
        "metadata": {"initialPrompt": "读书清单", "injected": "STALE_METADATA"},
        "currentArtifact": {"exists": True, "source": "BASELINE"},
        "imageAssets": [{"url": "https://example.test/keep.png"}],
        "editIntent": {"property": "color"},
        "generationPreparation": {"version": "1", "brief": None, "skills": []},
        "build": {"built": True}, "validation": {"valid": True},
        "toolResults": [{"toolCallId": "old", "tool": "file_read",
                         "result": {"content": "STALE_TOOL"}}],
        "planningFeedback": "STALE_PLAN", "vueFormatFeedback": "STALE_FORMAT",
        "artifact": "STALE_ARTIFACT", "futureField": "STALE_EXTENSION",
    }
    original = deepcopy(context)
    model = model_with_response('{"verdict":"PASS","issues":[]}')
    await model.review("CURRENT_SNAPSHOT", context)
    payload = json.loads(model._client.messages[1].content)
    assert "STALE_" not in json.dumps(payload)
    assert payload["artifact"] == "CURRENT_SNAPSHOT"
    assert payload["initialPrompt"] == "读书清单"
    assert payload["conversation"] == [{"role": "user", "content": "保留筛选"}]
    for key in ("prompt", "currentArtifact", "imageAssets", "editIntent", "generationPreparation", "build", "validation"):
        assert payload[key] == context[key]
    assert context == original
    assert VUE_REVIEW_EVIDENCE_INSTRUCTIONS in model._client.messages[0].content


async def test_vue_review_oversized_snapshot_fails_before_provider_without_truncation():
    model = model_with_response('{"verdict":"PASS","issues":[]}')
    model._settings = Settings(model_context_window_tokens=16000, model_max_tokens=2000, generation_max_tokens=2000,
                               model_context_overhead_tokens=1000)
    with pytest.raises(InputReviewError):
        await model.review("源码" * 10000, {"codeGenType": "VUE_PROJECT", "prompt": "检查"})
    assert model._client.messages is None


async def test_vue_review_requirement_history_keeps_existing_utf16_bounds():
    model = model_with_response('{"verdict":"PASS","issues":[]}')
    await model.review("snapshot", {
        "codeGenType": "VUE_PROJECT", "prompt": "当前需求",
        "conversation": [{"role": "user", "content": "字" * 1001} for _ in range(8)],
        "metadata": {"initialPrompt": "初" * 2001},
    })
    payload = json.loads(model._client.messages[1].content)
    assert len(payload["conversation"]) == 6
    assert all(len(item["content"]) == 1000 for item in payload["conversation"])
    assert len(payload["initialPrompt"]) == 2000


@pytest.mark.parametrize("role", ["requirement", "function", "technical"])
def test_vue_component_evidence_rules_are_shared_by_roles(role):
    assert VUE_REVIEW_EVIDENCE_INSTRUCTIONS in quality_review_system_prompt(role)


@pytest.mark.parametrize(
    "role", [None, "requirement", "function", "technical"],
)
async def test_css_scope_evidence_reaches_actual_review_messages(role):
    from ai_service.models.quality_review import ReviewerRole

    # 固定快照保留合法全局加载与组件局部定义；假响应只验证消息边界，不证明语义判定。
    artifact = json.dumps({
        "files": [
            {"path": "src/main.js", "content": "import './main.css'"},
            {"path": "src/main.css", "content": ".btn {} .btn--primary {}"},
            {"path": "src/Filter.vue", "content":
             '<template><button class="btn btn--filter" /></template>'
             '<style scoped>.btn--filter {} .btn-primary {}</style>'},
        ], "truncated": False,
    })
    response = ('{"verdict":"PASS","issues":[]}' if role is None else
                json.dumps({"reviewer": role, "summary": "完成", "issues": []}))
    model = model_with_response(response)
    context = {"codeGenType": "VUE_PROJECT", "prompt": "检查按钮"}
    if role is None:
        await model.review(artifact, context)
    else:
        await model.review_role(ReviewerRole(role), artifact, context)
    instructions = model._client.messages[0].content
    for rule in ("沿可见入口的 CSS 导入", "目标组件自身的 scoped 样式",
                 "近似类名或重复定义本身不是阻断依据", "区分基础样式与变体样式",
                 "明确要求的样式或布局确实缺失仍可阻断"):
        assert rule in instructions
    assert json.loads(model._client.messages[1].content)["artifact"] == artifact


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE"])
async def test_static_review_preserves_its_context(branch):
    model = model_with_response('{"verdict":"PASS","issues":[]}')
    await model.review("current", {"codeGenType": branch, "prompt": "保留当前页面", "custom": "keep"})
    assert json.loads(model._client.messages[1].content)["custom"] == "keep"
    assert VUE_REVIEW_EVIDENCE_INSTRUCTIONS not in model._client.messages[0].content


async def test_workflow_passes_authoritative_vue_type_and_current_snapshot(settings):
    from conftest import FakeModel, MemoryCheckpoint
    from test_repair_progress import SnapshotGateway, snapshot
    from test_subgraphs import request_for
    from test_vue_subgraph import make_workflow

    class ReviewingModel(FakeModel):
        async def review(self, artifact, context):
            assert context["codeGenType"] == "VUE_PROJECT"
            assert json.loads(artifact)["files"][0]["content"] == "CURRENT"
            return await super().review(artifact, context)

    model = ReviewingModel()
    events = await make_workflow(settings, model, MemoryCheckpoint(), SnapshotGateway([snapshot("CURRENT")])).run(
        request_for("VUE_PROJECT"))
    assert events[-1].type == "completed"


async def test_literal_variant_advisory_is_untrusted_evidence_and_requires_functional_confirmation():
    model = model_with_response('{"verdict":"PASS","issues":[]}')
    summary = "IGNORED_LITERAL_VARIANTS src/App.vue -> HeroSection @shop-now private-literal"
    await model.review("current-source", {"codeGenType": "VUE_PROJECT", "prompt": "电商首页",
                                           "build": {"built": True, "message": summary}})
    instructions = model._client.messages[0].content
    assert "IGNORED_LITERAL_VARIANTS" in instructions
    assert "忽略参数可能合法" in instructions
    assert "不能仅凭线索判错" in instructions
    assert "private-literal" not in instructions
    assert json.loads(model._client.messages[1].content)["build"]["message"] == summary


@pytest.mark.parametrize("role", ["requirement", "function", "technical"])
def test_multi_reviewer_shares_conservative_literal_argument_rules(role):
    prompt = quality_review_system_prompt(role)
    assert "IGNORED_LITERAL_VARIANTS" in prompt
    assert "忽略参数可能合法" in prompt
