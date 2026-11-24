"""图片素材策略的离线流程回归，不替代真实页面渲染验收。"""

import json

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.models.quality_review import ReviewerRole
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph, vue_recursion_limit
from conftest import ChangingSnapshotGateway, FakeModel, MemoryCheckpoint
from test_openai_compatible import model_with_response
from test_subgraphs import CleaningGraphCheckpoint, request_for
from test_vue_subgraph import initial, make_workflow, paused_vue_state


IMAGE_URL = "https://example.test/analytics.jpg"


class ImageGateway(ChangingSnapshotGateway):
    async def invoke(self, name, arguments, **kwargs):
        result = await super().invoke(name, arguments, **kwargs)
        if name == "image_search":
            return {"ok": True, "images": [{"url": IMAGE_URL, "description": "数据看板照片"}]}
        return result


class DashboardModel(FakeModel):
    async def generate(self, branch, context):
        self.calls.append(("generate", {"context": context}))
        return self.dashboard_turn(context, ":vue-generate:")

    async def repair(self, artifact, context):
        self.calls.append(("repair", {"context": context}))
        return self.dashboard_turn(context, ":vue-repair:")

    def dashboard_turn(self, context, prefix):
        if not any(prefix in item["toolCallId"] for item in context["toolResults"]):
            return ModelTurn("写入后台", tool_calls=[ToolCall("file_write", {
                "relativeFilePath": "src/App.vue",
                "content": "<template><main>订单和销售数据</main></template>",
            })])
        return ModelTurn("完成")


@pytest.mark.parametrize("repair", [False, True])
async def test_dashboard_optional_images_reach_build_and_review_including_repair(settings, repair):
    model = DashboardModel(reviews=[False, True] if repair else [True])
    gateway = ImageGateway()
    request = request_for("VUE_PROJECT").model_copy(update={
        "prompt": "做一个电商运营后台，看订单和销售数据",
        # 同名不可信 metadata 不得将素材策略提升成要求。
        "metadata": {"imageUsagePolicy": "required"},
    })
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request)
    assert events[-1].type == "completed"
    stages = [data["context"] for name, data in model.calls if name in {"generate", "repair", "review"}]
    assert all(context["imageUsagePolicy"] == "optional" for context in stages)
    assert all(context["imageAssets"][0]["url"] == IMAGE_URL for context in stages)
    assert not any(context.get("imageUsageFeedback") for context in stages)
    assert sum(call["name"] == "image_search" for call in gateway.calls) == 1
    assert sum(call["name"] == "project_build" for call in gateway.calls) == (2 if repair else 1)
    assert sum(name == "review" for name, _ in model.calls) == (2 if repair else 1)
    assert sum(name == "repair" for name, _ in model.calls) == (2 if repair else 0)
    assert all(IMAGE_URL not in call["arguments"].get("content", "")
               for call in gateway.calls if call["name"] == "file_write")


@pytest.mark.parametrize("exists", [False, True])
async def test_explicit_image_request_still_requires_usage_before_build(settings, exists):
    model = DashboardModel()
    gateway = ImageGateway(artifact_context={"exists": exists})
    request = request_for("VUE_PROJECT").model_copy(update={
        "prompt": "不要替换原有图片，但请给首页添加照片。",
        "metadata": {"imageUsagePolicy": "optional"},
    })
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request)
    assert events[-1].error.code == "VUE_IMAGE_USAGE_MISSING"
    contexts = [data["context"] for name, data in model.calls if name == "generate"]
    assert all(context["imageUsagePolicy"] == "required" for context in contexts)
    assert sum(bool(context.get("imageUsageFeedback")) for context in contexts) == 2
    assert not any(call["name"] == "project_build" for call in gateway.calls)


@pytest.mark.parametrize("node", ["vue_agent", "repair"])
@pytest.mark.parametrize("policy", [None, "required", "optional", "unknown"])
async def test_subgraph_only_explicit_optional_policy_relaxes_legacy_gate(settings, node, policy):
    model = FakeModel()
    context = {"imageAssets": [{"url": IMAGE_URL}]}
    if policy is not None:
        context["imageUsagePolicy"] = policy
    graph = build_vue_tool_subgraph(
        make_workflow(settings, model, MemoryCheckpoint()),
        EventEmitter("subgraph-test"), "42:subgraph-test", {}, node=node,
    )
    state = {**initial(), "context": context}
    if policy == "optional":
        result = await graph.ainvoke(state)
        assert result["context"]["imageUsagePolicy"] == policy
    else:
        with pytest.raises(ValueError, match="VUE_IMAGE_USAGE_MISSING"):
            await graph.ainvoke(state)
        assert sum(name == ("repair" if node == "repair" else "generate")
                   for name, _ in model.calls) == 3


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE"])
async def test_static_generation_does_not_add_vue_image_policy(settings, branch):
    model = FakeModel()
    gateway = ImageGateway()
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for(branch))
    assert events[-1].type == "completed"
    assert not any(call["name"] == "image_search" for call in gateway.calls)
    assert all("imageUsagePolicy" not in data["context"]
               for name, data in model.calls if name in {"generate", "review"})


@pytest.mark.parametrize("phase", ["generate", "repair", "review", *ReviewerRole])
async def test_policy_and_instructions_reach_provider_messages(phase):
    response = ('{"content":"完成","toolCalls":[]}' if phase in {"generate", "repair"} else
                '{"verdict":"PASS","issues":[]}' if phase == "review" else
                json.dumps({"reviewer": phase.value, "summary": "完成", "issues": []}))
    model = model_with_response(response)
    context = {"codeGenType": "VUE_PROJECT", "prompt": "看订单和销售数据",
               "imageUsagePolicy": "optional", "imageAssets": [{"url": IMAGE_URL}]}
    if phase == "generate":
        await model.generate("VUE_PROJECT", context)
    elif phase == "repair":
        await model.repair("snapshot", context)
    elif phase == "review":
        await model.review("snapshot", context)
    else:
        await model.review_role(phase, "snapshot", context)
    system, human = model._client.messages
    assert "imageUsagePolicy" in system.content
    assert "optional" in system.content
    assert json.loads(human.content)["imageUsagePolicy"] == "optional"


async def test_workflow_multi_reviewer_receives_optional_policy(settings):
    model = DashboardModel()
    events = await make_workflow(
        settings.model_copy(update={"multi_agent_review_enabled": True}),
        model, MemoryCheckpoint(), ImageGateway(),
    ).run(request_for("VUE_PROJECT"))
    assert events[-1].type == "completed"
    contexts = [data["context"] for name, data in model.calls if name == "review_role"]
    assert len(contexts) == 3
    assert all(context["imageUsagePolicy"] == "optional" for context in contexts)


@pytest.mark.parametrize("repair", [False, True])
async def test_policy_survives_parent_graph_rebuild_at_saved_model_turn(settings, monkeypatch, repair):
    import ai_service.orchestration.workflow as workflow_module

    original = workflow_module.build_vue_tool_subgraph

    def interrupted(*args, **kwargs):
        if (kwargs.get("node") == "repair") == repair:
            kwargs["interrupt_after"] = ["model_turn"]
        return original(*args, **kwargs)

    model = DashboardModel(reviews=[False, True] if repair else [True])
    checkpoint = CleaningGraphCheckpoint()
    workflow = make_workflow(settings, model, checkpoint, ImageGateway())
    config = {"configurable": {"thread_id": "42:subgraph-test"},
              "recursion_limit": vue_recursion_limit(settings)}
    monkeypatch.setattr(workflow_module, "build_vue_tool_subgraph", interrupted)
    graph = workflow._build_graph(EventEmitter("subgraph-test"), "42:subgraph-test", {})
    await graph.ainvoke(initial(), config)
    saved = await paused_vue_state(graph, config, repair)
    assert saved.values["context"]["imageUsagePolicy"] == "optional"
    assert saved.values["vue_loop"]["image_used"] is True
    assert not saved.values["context"].get("imageAssetsApplied")

    # 从父检查点续跑，已保存的模型轮次不因策略初始化而重复调用。
    monkeypatch.setattr(workflow_module, "build_vue_tool_subgraph", original)
    rebuilt = workflow._build_graph(EventEmitter("subgraph-test"), "42:subgraph-test", {})
    result = await rebuilt.ainvoke(None, config)
    assert result["quality_passed"] is True
    assert result["context"]["imageUsagePolicy"] == "optional"
    assert result["tool_call_count"] == (2 if repair else 1)
