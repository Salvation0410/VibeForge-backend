"""生成额度必须同时约束实际请求和预算，不能污染共享的客服/审查客户端。"""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage
from pydantic import ValidationError

from ai_service.config import Settings
from ai_service.models import openai_compatible
from ai_service.models.input_review import InputReviewError
from ai_service.models.openai_compatible import OpenAICompatibleModel


def quota_settings(**values):
    return Settings(_env_file=None, internal_bearer_token="test", spring_gateway_base_url="http://spring.test",
                    spring_gateway_bearer_token="test", **values)


class QuotaClient:
    def __init__(self, **options):
        self.options = options
        self.requests = []
        self.content = '{"content":"done","toolCalls":[]}'

    async def ainvoke(self, messages, **options):
        self.requests.append(options)
        return SimpleNamespace(content=self.content, response_metadata={"finish_reason": "stop"}, usage_metadata={})

    async def astream(self, messages, **options):
        self.requests.append(options)
        yield AIMessageChunk(content="<html>done</html>", response_metadata={"finish_reason": "stop"})


def quota_model(monkeypatch, **settings):
    monkeypatch.setattr(openai_compatible, "ChatOpenAI", QuotaClient)
    return OpenAICompatibleModel(quota_settings(**settings))


def test_generation_defaults_and_context_validation():
    settings = quota_settings()
    assert settings.generation_max_tokens == 40960
    assert settings.model_max_tokens == 8192
    with pytest.raises(ValidationError):
        quota_settings(generation_max_tokens=0)
    with pytest.raises(ValidationError):
        quota_settings(model_context_window_tokens=45056)


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE", "VUE_PROJECT"])
async def test_generate_and_repair_override_only_request_quota(monkeypatch, branch):
    model = quota_model(monkeypatch)
    await model.generate(branch, {"prompt": "页面"})
    await model.repair("old", {"codeGenType": branch})
    assert model._client.requests == [{"max_tokens": 40960}, {"max_tokens": 40960}]
    assert model._client.options["max_tokens"] == 8192
    model._client.content = "HTML"
    await model.route("页面")
    model._client.content = '{"verdict":"PASS","issues":[]}'
    await model.review("页面", {"codeGenType": branch})
    assert model._client.requests[-2:] == [{}, {}]


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE"])
async def test_stream_generate_and_repair_use_generation_quota(monkeypatch, branch):
    model = quota_model(monkeypatch)
    deltas = []

    async def emit(text):
        deltas.append(text)

    await model.generate_stream(branch, {}, on_delta=emit)
    await model.repair_stream("old", {"codeGenType": branch}, on_delta=emit)
    assert all(r["max_tokens"] == 40960 for r in model._client.requests)
    assert len(deltas) == 2


@pytest.mark.asyncio
async def test_generation_budget_reserves_requested_output_before_network(monkeypatch):
    model = quota_model(monkeypatch, model_context_window_tokens=65536)
    messages = [HumanMessage(content="x" * 25000)]
    model._check_budget(messages)
    with pytest.raises(InputReviewError, match="INPUT_CONTEXT_BUDGET_EXCEEDED"):
        await model._invoke(messages, generation=True)
    assert model._client.requests == []


@pytest.mark.asyncio
async def test_generation_quota_can_be_explicitly_overridden(monkeypatch):
    model = quota_model(monkeypatch, generation_max_tokens=20000)
    await model.generate("HTML", {})
    assert model._client.requests == [{"max_tokens": 20000}]
