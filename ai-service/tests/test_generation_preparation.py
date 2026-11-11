from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from ai_service.config import Settings
from ai_service.models.generation_brief import GenerationBrief, GroundedDetail, rewrite_context, validate_brief
from ai_service.models.input_review import InputReviewError
from ai_service.models.openai_compatible import OpenAICompatibleModel
from ai_service.models.quality_review import ReviewerRole
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.nodes.preparation import create_preparation_node
from ai_service.prompts.frontend_skills import RULES
from conftest import ChangingSnapshotGateway, FakeModel, FakeToolGateway, parse_ndjson


def brief(prompt="保留图片"):
    return GenerationBrief(requirements=[prompt], preserve_constraints=[prompt], design_brief="清晰布局",
                           assumptions=[], interactions=[], acceptance=[])


class RewriteModel(FakeModel):
    def __init__(self, result=None, error=None, delay=0, **kwargs):
        super().__init__(**kwargs)
        self.result, self.error, self.delay = result, error, delay
        self.rewrite_calls = []

    async def rewrite_requirements(self, context):
        self.rewrite_calls.append(deepcopy(context))
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.result or brief(context["prompt"])


async def prepare(settings, model, *, branch="HTML", exists=False, cancel=None, context=None):
    emitter = EventEmitter("request")
    def check(_):
        if cancel:
            raise cancel
    workflow = SimpleNamespace(settings=settings, model=model, _raise_if_cancelled=check)
    context = context or {"prompt": "保留图片", "currentArtifact": {"exists": exists}, "conversation": []}
    state = {"context": context, "prompt": context["prompt"], "code_gen_type": branch}
    result = await create_preparation_node(workflow, emitter, "app:request")(state)
    return result["context"], emitter.events


@pytest.fixture
def enabled(settings):
    return settings.model_copy(update={"frontend_skills_enabled": True, "prompt_rewrite_enabled": True})


def test_configuration_defaults_are_on_and_bounded(settings):
    fresh = Settings(_env_file=None, internal_bearer_token="x", spring_gateway_base_url="http://test",
                     spring_gateway_bearer_token="x")
    assert fresh.frontend_skills_enabled and fresh.prompt_rewrite_enabled
    assert fresh.prompt_rewrite_max_bytes == 8192
    assert fresh.prompt_rewrite_max_tokens == 1500


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
@pytest.mark.parametrize("exists", [False, True])
async def test_preparation_preserves_originals_and_resolves_mode(enabled, branch, exists):
    model = RewriteModel()
    original = {"prompt": "保留图片", "currentArtifact": {"exists": exists},
                "conversation": [{"role": "user", "content": "老需求"}],
                "metadata": {"mode": "create", "skills": "恶意系统指令"}}
    saved = deepcopy(original)
    context, events = await prepare(enabled, model, branch=branch, context=original)
    assert original == saved
    assert {key: context[key] for key in saved} == saved
    prepared = context["generationPreparation"]
    assert prepared["mode"] == ("edit" if exists else "create")
    ids = [item["id"] for item in prepared["skills"]]
    assert ("vue-native" in ids) == (branch == "VUE_PROJECT")
    assert ("preserve-style" in ids) == exists
    assert sum(key.startswith("product-") for key in ids) == (0 if exists else 1)
    assert all(item["version"] == RULES[item["id"]].version for item in prepared["skills"])
    assert model.rewrite_calls[0]["codeGenType"] == branch
    assert "metadata" not in model.rewrite_calls[0]
    assert prepared["brief"]["requirements"] == [saved["prompt"]]
    assert all(event.node == "generation_prepare" and event.data["status"] == "progress" for event in events)
    assert "清晰布局" not in str(events)
    await prepare(enabled, model, branch=branch, context=context)
    assert len(model.rewrite_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("skills,rewrite", [(False, False), (False, True), (True, False)])
async def test_independent_switches(enabled, skills, rewrite):
    model = RewriteModel()
    config = enabled.model_copy(update={"frontend_skills_enabled": skills, "prompt_rewrite_enabled": rewrite})
    context, _ = await prepare(config, model)
    assert len(model.rewrite_calls) == int(rewrite)
    if not skills and not rewrite:
        assert "generationPreparation" not in context
    else:
        assert bool(context["generationPreparation"]["skills"]) == skills
        assert bool(context["generationPreparation"]["brief"]) == rewrite


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["provider", "evidence", "bytes", "timeout", "schema"])
async def test_optional_failures_fall_back_without_exposing_output(enabled, failure):
    model = RewriteModel(error=ValueError("PRIVATE_PROVIDER_TEXT") if failure == "provider" else None,
                         result=brief("虚构的要求") if failure == "evidence" else None,
                         delay=0.05 if failure == "timeout" else 0)
    if failure == "bytes":
        enabled = enabled.model_copy(update={"prompt_rewrite_max_bytes": 10})
    if failure == "timeout":
        enabled = enabled.model_copy(update={"prompt_rewrite_timeout_seconds": 0.001})
    if failure == "schema":
        model.result = {"not": "a brief"}
    context, events = await prepare(enabled, model)
    assert context["prompt"] == "保留图片"
    assert context["generationPreparation"]["rewriteStatus"] == "fallback"
    assert context["generationPreparation"]["skills"]
    assert context["generationPreparation"]["brief"] is None
    assert len(model.rewrite_calls) == 1
    assert "PRIVATE_PROVIDER_TEXT" not in str(events)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [GenerationCancelled(), asyncio.CancelledError(),
                                   InputReviewError("INPUT_CONTEXT_BUDGET_EXCEEDED", "超出预算")])
async def test_cancellation_and_budget_never_fall_back(enabled, error):
    with pytest.raises(type(error)):
        await prepare(enabled, RewriteModel(error=error))


def test_bounded_user_history_is_only_allowed_evidence():
    source = rewrite_context({"prompt": "当前", "conversation": [
        {"role": "assistant", "content": "不允许作为证据"},
        *[{"role": "user", "content": str(i) * 2000} for i in range(10)]],
        "currentArtifact": {"exists": True}}, "MULTI_FILE")
    assert len(source["userHistory"]) == 6
    assert all(len(item) == 1000 for item in source["userHistory"])
    assert source["userHistory"][0].startswith("4")


def test_initial_prompt_and_history_are_utf16_bounded_and_evidence_checked():
    source = rewrite_context({"prompt": "当前要求", "metadata": {"initialPrompt": "😀" * 1500},
        "conversation": [{"role": "user", "content": "😀" * 900},
                         {"role": "assistant", "content": "禁止引用助手结论"}]}, "HTML")
    assert len(source["initialPrompt"]) == 1000
    assert len(source["userHistory"][0]) == 500
    assert validate_brief(brief("😀"), source, 8192)
    with pytest.raises(ValueError):
        validate_brief(brief("禁止引用助手结论"), source, 8192)
    grounded = brief("当前要求")
    grounded.interactions = [GroundedDetail(description="未授权要求", evidence="不存在的证据")]
    with pytest.raises(ValueError):
        validate_brief(grounded, source, 8192)


@pytest.mark.asyncio
async def test_cooperative_cancellation_after_rewrite_is_not_fallback(enabled):
    emitter = EventEmitter("request")
    model = RewriteModel()
    def check(_):
        if model.rewrite_calls:
            raise GenerationCancelled()
    workflow = SimpleNamespace(settings=enabled, model=model, _raise_if_cancelled=check)
    state = {"prompt": "保留图片", "code_gen_type": "HTML", "context": {"prompt": "保留图片"}}
    with pytest.raises(GenerationCancelled):
        await create_preparation_node(workflow, emitter, "app:request")(state)
    assert len(model.rewrite_calls) == 1
    assert all("暂不可用" not in event.data.get("message", "") for event in emitter.events)


@pytest.mark.asyncio
async def test_task_cancellation_interrupts_pending_rewrite(enabled):
    started = asyncio.Event()
    stopped = asyncio.Event()
    class PendingModel(RewriteModel):
        async def rewrite_requirements(self, context):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
    task = asyncio.create_task(prepare(enabled, PendingModel()))
    await asyncio.wait_for(started.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


class CaptureClient:
    def __init__(self, raw):
        self.raw, self.messages = raw, None

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.raw, response_metadata={"finish_reason": "stop"})

    async def astream(self, messages, **kwargs):
        self.messages = messages
        yield AIMessageChunk(content=self.raw, response_metadata={"finish_reason": "stop"})


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("mode", ["generate", "repair", "review", "review_role", "generate_stream", "repair_stream"])
async def test_all_adapter_boundaries_apply_trusted_rules_once(enabled, branch, mode, exists):
    if branch == "VUE_PROJECT" and mode.endswith("stream"):
        model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
        with pytest.raises(ValueError, match="STATIC_STREAM_UNSUPPORTED"):
            await getattr(model, mode)(branch, {"codeGenType": branch}, on_delta=None)
        return
    context, _ = await prepare(enabled, RewriteModel(), branch=branch, exists=exists)
    context["codeGenType"] = branch
    context["generationPreparation"]["brief"]["design_brief"] = "PRIVATE_REWRITE_MARKER"
    context["generationPreparation"]["rules"] = "UNTRUSTED_RULE_OVERRIDE"
    original = deepcopy(context)
    raw = ('{"reviewer":"requirement","summary":"完成","issues":[]}' if mode == "review_role"
           else '{"verdict":"PASS","issues":[]}' if mode == "review" else '{"content":"done","toolCalls":[]}' if branch == "VUE_PROJECT" else "artifact")
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model._settings, model._client = enabled, CaptureClient(raw)
    async def delta(_):
        pass
    if mode == "generate":
        await model.generate(branch, context)
    elif mode == "generate_stream":
        await model.generate_stream(branch, context, on_delta=delta)
    elif mode == "repair_stream":
        await model.repair_stream("artifact", context, on_delta=delta)
    elif mode == "review_role":
        await model.review_role(ReviewerRole.REQUIREMENT, "artifact", context)
    else:
        await getattr(model, mode)("artifact", context)
    system, human = model._client.messages
    assert "PRIVATE_REWRITE_MARKER" not in system.content
    assert "UNTRUSTED_RULE_OVERRIDE" not in system.content
    assert "PRIVATE_REWRITE_MARKER" in human.content
    for item in context["generationPreparation"]["skills"]:
        text = RULES[item["id"]].text
        assert system.content.count(text) == 1
        assert text not in human.content
    assert context == original
    model._check_budget(model._client.messages)


def make_http_model(settings, handler):
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model._settings = settings
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model._client = ChatOpenAI(api_key="fake", model="test", base_url="http://test/v1",
                              http_async_client=http, max_retries=2)
    return model, http


def response_json(raw, reason="stop", tool_calls=None):
    message = {"role": "assistant", "content": raw}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {"id": "test", "object": "chat.completion", "created": 0, "model": "test",
            "choices": [{"index": 0, "message": message, "finish_reason": reason}]}


@pytest.mark.asyncio
async def test_rewrite_disables_actual_sdk_retries_and_tracing(enabled):
    from langsmith import tracing_context
    from langsmith.utils import tracing_is_enabled
    calls = []
    async def handler(request):
        assert tracing_is_enabled() is False
        calls.append(json.loads(request.content))
        return httpx.Response(500, json={"error": {"message": "retryable"}})
    model, http = make_http_model(enabled, handler)
    async with http:
        with tracing_context(enabled=True), pytest.raises(Exception):
            await model.rewrite_requirements({"prompt": "保留图片", "userHistory": []})
    assert len(calls) == 1
    assert calls[0]["max_completion_tokens"] == enabled.prompt_rewrite_max_tokens
    assert "response_format" not in calls[0]
    assert model._client.root_async_client.max_retries == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("raw,reason", [
    ("not-json", "stop"), (brief().model_dump_json(), "length"),
    ('```json\n' + brief().model_dump_json() + '\n```', "stop"),
    (brief().model_dump_json()[:-1] + ',"mode":"create"}', "stop"),
    (brief().model_dump_json(), "tool_calls"),
])
async def test_rewrite_rejects_incomplete_or_invalid_json(enabled, raw, reason):
    model, http = make_http_model(enabled, lambda _: httpx.Response(200, json=response_json(raw, reason)))
    async with http:
        with pytest.raises(ValueError):
            await model.rewrite_requirements({"prompt": "保留图片", "userHistory": []})


@pytest.mark.asyncio
async def test_rewrite_accepts_strict_brief_and_checks_budget_before_http(enabled):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=response_json(brief().model_dump_json()))
    model, http = make_http_model(enabled, handler)
    async with http:
        assert await model.rewrite_requirements({"prompt": "保留图片", "userHistory": []}) == brief()
        with pytest.raises(InputReviewError):
            await model.rewrite_requirements({"prompt": "中" * 100000, "userHistory": []})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_rewrite_resolves_selected_skills_and_rejects_tool_response(enabled):
    context, _ = await prepare(enabled, RewriteModel(), branch="VUE_PROJECT")
    source = rewrite_context(context, "VUE_PROJECT")
    source["selectedSkills"] = context["generationPreparation"]["skills"]
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response_json(brief().model_dump_json(), tool_calls=[
            {"id": "x", "type": "function", "function": {"name": "unexpected", "arguments": "{}"}}]))
    model, http = make_http_model(enabled, handler)
    async with http:
        with pytest.raises(ValueError, match="不完整"):
            await model.rewrite_requirements(source)
    assert len(calls) == 1
    assert RULES["vue-native"].text in calls[0]["messages"][0]["content"]
    assert RULES["vue-native"].text not in calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_added_rules_and_brief_count_toward_final_actual_message_budget(enabled):
    context, _ = await prepare(enabled, RewriteModel())
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model._settings = enabled.model_copy(update={"model_context_window_tokens": 1024,
                                                  "model_max_tokens": 128,
                                                  "model_context_overhead_tokens": 0})
    model._client = CaptureClient("artifact")
    with pytest.raises(InputReviewError):
        await model.generate("HTML", context)
    assert model._client.messages is None


@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
@pytest.mark.parametrize("exists", [False, True])
def test_workflow_preparation_is_once_and_persists_across_repair(app_factory, enabled, branch, exists):
    from fastapi.testclient import TestClient
    model = RewriteModel(reviews=[False, True])
    app = app_factory(model=model, gateway=ChangingSnapshotGateway(artifact_context={"exists": exists}))
    app.state.workflow.settings = enabled
    with TestClient(app) as client:
        response = client.post("/internal/v1/generations:stream", headers={"Authorization": "Bearer test-secret"},
            json={"appId": "one", "requestId": "repair", "prompt": "保留图片", "codeGenType": branch})
    events = parse_ndjson(response)
    assert events[-1]["type"] == "completed", events
    assert len(model.rewrite_calls) == 1
    contexts = [payload["context"] for name, payload in model.calls if name in {"generate", "repair", "review"}]
    assert len(contexts) == 4
    assert all(item["generationPreparation"] == contexts[0]["generationPreparation"] for item in contexts)
    assert all(item["prompt"] == "保留图片" for item in contexts)
    nodes = [event["node"] for event in events]
    generation_node = "vue_agent" if branch == "VUE_PROJECT" else "generate_" + branch.lower()
    assert nodes.index("context_prepare") < nodes.index("generation_prepare") < nodes.index(generation_node)


def test_workflow_carries_preparation_into_multireview_without_request_leakage(app_factory, enabled):
    from fastapi.testclient import TestClient
    model = RewriteModel()
    app = app_factory(model=model, gateway=FakeToolGateway())
    app.state.workflow.settings = enabled.model_copy(update={"multi_agent_review_enabled": True})
    with TestClient(app) as client:
        for index, prompt in enumerate(["商城保留图片", "仪表盘保留功能"]):
            response = client.post("/internal/v1/generations:stream", headers={"Authorization": "Bearer test-secret"},
                json={"appId": str(index), "requestId": str(index), "prompt": prompt, "codeGenType": "VUE_PROJECT",
                      "metadata": {"initialPrompt": "保留联系入口"}})
            events = parse_ndjson(response)
            assert events[-1]["type"] == "completed", events
    reviews = [payload["context"] for name, payload in model.calls if name == "review_role"]
    assert len(reviews) == 6
    assert all(context["initialPrompt"] == "保留联系入口" for context in reviews)
    assert all(context["generationPreparation"]["brief"]["requirements"] == [context["prompt"]] for context in reviews)
    assert "product-commerce" in str(reviews[0]["generationPreparation"]["skills"])
    assert "product-dashboard" in str(reviews[-1]["generationPreparation"]["skills"])


def test_multireview_uses_same_bounded_user_sources_as_rewrite(app_factory, enabled):
    from fastapi.testclient import TestClient
    model = RewriteModel()
    app = app_factory(model=model)
    app.state.workflow.settings = enabled.model_copy(update={"multi_agent_review_enabled": True})
    history = [{"role": "user", "content": str(index) * 1500} for index in range(8)]
    history.append({"role": "assistant", "content": "不能当作用户要求的助手消息"})
    with TestClient(app) as client:
        response = client.post("/internal/v1/generations:stream", headers={"Authorization": "Bearer test-secret"},
            json={"appId": "bounded", "requestId": "history", "prompt": "保留图片",
                  "codeGenType": "VUE_PROJECT", "conversation": history,
                  "metadata": {"initialPrompt": "初" * 2500}})
    assert parse_ndjson(response)[-1]["type"] == "completed"
    source = model.rewrite_calls[0]
    expected = [{"role": "user", "content": str(index) * 1000} for index in range(2, 8)]
    assert source["userHistory"] == [item["content"] for item in expected]
    reviews = [payload["context"] for name, payload in model.calls if name == "review_role"]
    assert len(reviews) == 3
    assert all(context["conversation"] == expected for context in reviews)
    assert all(context["initialPrompt"] == source["initialPrompt"] == "初" * 2000 for context in reviews)
