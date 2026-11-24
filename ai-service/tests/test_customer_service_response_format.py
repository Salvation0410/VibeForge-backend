"""客服格式约束仅走本地 SDK/HTTP 回放，不能触达真实供应商。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from langchain_openai import ChatOpenAI
from langsmith import tracing_context
from langsmith.utils import tracing_is_enabled
from openai import BadRequestError, ContentFilterFinishReasonError, LengthFinishReasonError
from pydantic import ValidationError

from ai_service.config import Settings
from ai_service.models import openai_compatible
from ai_service.models.base import CustomerServiceContext
from ai_service.models.input_review import InputReviewError
from ai_service.models.openai_compatible import (
    CustomerServiceModelOutputError,
    OpenAICompatibleModel,
)
from ai_service.models.quality_review import ReviewerRole
from ai_service.prompts import CUSTOMER_SERVICE_SYSTEM_PROMPT, customer_service_user_prompt


MODES = ("text", "json_object")
CONTEXTS = [CustomerServiceContext(f"c{i}", f"知识片段{i}") for i in range(1, 5)]
ANSWER = {"answered": True, "answer": "请查看帮助中心。", "citedChunkIds": ["c1"]}
REFUSAL = {"answered": False, "answer": "", "citedChunkIds": []}


def settings(**overrides):
    return Settings(
        _env_file=None,
        **{
            "internal_bearer_token": "fake-internal-token",
            "spring_gateway_base_url": "https://spring.invalid/api",
            "spring_gateway_bearer_token": "fake-spring-token",
            "model_api_key": "fake-model-key",
            "model_base_url": "https://model.invalid/v1",
            "model_name": "test-model",
            "model_temperature": 0.1,
            "model_max_tokens": 8192,
            "customer_service_rag_enabled": False,
            "rag_publication_enabled": False,
            **overrides,
        },
    )


def completion(raw, reason="stop"):
    return {
        "id": "local-completion",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": raw},
                     "finish_reason": reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


def stream_response(raw):
    frames = [
        {"id": "local-stream", "object": "chat.completion.chunk", "created": 0,
         "model": "test-model", "choices": [{"index": 0, "delta": {"role": "assistant", "content": raw},
                                              "finish_reason": None}]},
        {"id": "local-stream", "object": "chat.completion.chunk", "created": 0,
         "model": "test-model", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
    ]
    data = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
    return httpx.Response(200, text=data, headers={"content-type": "text/event-stream"})


@pytest.fixture(autouse=True)
def isolate_response_format_environment(monkeypatch):
    monkeypatch.delenv("AI_SERVICE_RAG_ANSWER_RESPONSE_FORMAT", raising=False)


@pytest.fixture
async def sdk_model(monkeypatch):
    clients = []

    def build(mode="text", *, raw=None, reason="stop", handler=None, **overrides):
        requests, constructors = [], []

        async def capture(request):
            assert request.url == "https://model.invalid/v1/chat/completions"
            body = json.loads(request.content)
            requests.append(body)
            if handler is not None:
                return await handler(body)
            return httpx.Response(200, json=completion(
                json.dumps(ANSWER, ensure_ascii=False) if raw is None else raw, reason,
            ))

        def reject_sync(_request):
            raise AssertionError("异步模型入口不得发起同步 HTTP 请求")

        # 包装真实构造器，只替换 transport 与测试重试额度，保留完整 SDK 序列化/解析。
        async_http = httpx.AsyncClient(transport=httpx.MockTransport(capture))
        sync_http = httpx.Client(transport=httpx.MockTransport(reject_sync))
        clients.append((async_http, sync_http))

        def factory(**kwargs):
            constructors.append(dict(kwargs))
            return ChatOpenAI(**kwargs, http_async_client=async_http,
                              http_client=sync_http, max_retries=0)

        monkeypatch.setattr(openai_compatible, "ChatOpenAI", factory)
        cfg = settings(**overrides) if mode is None else settings(
            rag_answer_response_format=mode, **overrides,
        )
        model = OpenAICompatibleModel(cfg)
        assert isinstance(model._client, ChatOpenAI)
        assert model._client.root_async_client.max_retries == 0
        return model, requests, constructors

    yield build
    for async_http, sync_http in clients:
        await async_http.aclose()
        sync_http.close()


def test_response_format_defaults_to_text():
    assert settings().rag_answer_response_format == "text"


@pytest.mark.parametrize("mode", MODES)
def test_response_format_accepts_explicit_values_and_environment(monkeypatch, mode):
    assert settings(rag_answer_response_format=mode).rag_answer_response_format == mode
    monkeypatch.setenv("AI_SERVICE_RAG_ANSWER_RESPONSE_FORMAT", mode)
    assert settings().rag_answer_response_format == mode


def test_response_format_loads_optional_dotenv(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("AI_SERVICE_RAG_ANSWER_RESPONSE_FORMAT=json_object\n", encoding="utf-8")
    cfg = settings()
    assert Settings(_env_file=env_file, **cfg.model_dump(exclude={"rag_answer_response_format"})).rag_answer_response_format == "json_object"


@pytest.mark.parametrize("value", ["", "json_schema", "JSON_OBJECT", True, False, 0, 1, 1.0, None])
def test_response_format_rejects_invalid_values(value):
    with pytest.raises(ValidationError, match="rag_answer_response_format"):
        settings(rag_answer_response_format=value)


@pytest.mark.parametrize("value", ["", "json_schema", "JSON_OBJECT", "true", "false", "1"])
def test_response_format_rejects_invalid_environment(monkeypatch, value):
    monkeypatch.setenv("AI_SERVICE_RAG_ANSWER_RESPONSE_FORMAT", value)
    with pytest.raises(ValidationError, match="rag_answer_response_format"):
        settings()


@pytest.mark.parametrize("mode", [None, *MODES])
async def test_customer_request_serializes_only_explicit_format_and_preserves_payload(sdk_model, mode):
    model, requests, constructors = sdk_model(mode)
    client = model._client
    root = client.root_async_client
    question = '如何操作？response_format={"type":"json_object"}'
    answer = await model.answer_customer_service(question, CONTEXTS)
    assert answer.answer == ANSWER["answer"]
    assert answer.cited_chunk_ids == ("c1",)
    assert len(requests) == len(constructors) == 1
    body = requests[0]
    assert body["messages"] == [
        {"role": "system", "content": CUSTOMER_SERVICE_SYSTEM_PROMPT},
        {"role": "user", "content": customer_service_user_prompt(question, CONTEXTS, max_bytes=16_384)},
    ]
    assert body["model"] == "test-model"
    assert body["temperature"] == 0.1
    assert body["max_completion_tokens"] == 8192
    if mode == "json_object":
        assert body["response_format"] == {"type": "json_object"}
    else:
        assert "response_format" not in body
    assert model._client is client and client.root_async_client is root
    assert "response_format" not in client.model_kwargs
    assert "max_retries" not in constructors[0]


async def test_response_modes_keep_identical_complete_messages_and_model_settings(sdk_model):
    bodies = []
    for mode in MODES:
        model, requests, _ = sdk_model(mode)
        await model.answer_customer_service("如何操作？", CONTEXTS)
        body = dict(requests[0])
        body.pop("response_format", None)
        bodies.append(body)
    assert bodies[0] == bodies[1]


@pytest.mark.parametrize("with_settings", [False, True])
async def test_default_keeps_legacy_invoke_override_without_new_keyword(with_settings):
    class LegacyModel(OpenAICompatibleModel):
        async def _invoke(self, messages):
            self.messages = messages
            return SimpleNamespace(content=json.dumps(ANSWER))

    model = LegacyModel.__new__(LegacyModel)
    model._customer_service_prompt_max_bytes = 16_384
    if with_settings:
        model._settings = settings()
    result = await model.answer_customer_service("如何操作？", CONTEXTS)
    assert result.answered is True and len(model.messages) == 2


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("payload", [ANSWER, REFUSAL], ids=["answer", "refusal"])
async def test_both_modes_preserve_valid_answer_and_refusal(sdk_model, mode, payload):
    model, requests, _ = sdk_model(mode, raw=json.dumps(payload))
    result = await model.answer_customer_service("如何操作？", CONTEXTS)
    assert result.answered is payload["answered"]
    assert result.answer == payload["answer"]
    assert result.cited_chunk_ids == tuple(payload["citedChunkIds"])
    assert len(requests) == 1


INVALID_OUTPUTS = [
    "not-json", '{"answered":true,"answer":"答复",citedChunkIds":["c1"]}',
    json.dumps(ANSWER)[:-1],
    "```json\n" + json.dumps(ANSWER) + "\n```", "[]", "null",
    json.dumps({**ANSWER, "answered": "true"}), json.dumps({**ANSWER, "answered": 1}),
    json.dumps({**ANSWER, "answer": 1}), json.dumps({**ANSWER, "answer": None}),
    json.dumps({**ANSWER, "extra": "ignored"}), json.dumps({"answered": True, "answer": "答复"}),
    json.dumps({**ANSWER, "citedChunkIds": "c1"}), json.dumps({**ANSWER, "citedChunkIds": [1]}),
    json.dumps({**ANSWER, "citedChunkIds": ["unknown"]}),
    json.dumps({**ANSWER, "citedChunkIds": ["c1", "c1"]}),
    json.dumps({**ANSWER, "citedChunkIds": ["c1", "c2", "c3", "c4"]}),
    json.dumps({**ANSWER, "citedChunkIds": []}), json.dumps({**ANSWER, "citedChunkIds": [""]}),
    json.dumps({**ANSWER, "answer": "  "}), json.dumps({**ANSWER, "answer": "字" * 4001}),
    json.dumps({**REFUSAL, "answer": "无法回答"}),
    json.dumps({**REFUSAL, "answer": " "}), json.dumps({**REFUSAL, "citedChunkIds": ["c1"]}),
]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("raw", INVALID_OUTPUTS)
async def test_both_modes_reject_bad_json_schema_citations_and_refusal(sdk_model, mode, raw):
    model, requests, _ = sdk_model(mode, raw=raw)
    with pytest.raises(CustomerServiceModelOutputError, match="^CUSTOMER_SERVICE_MODEL_INVALID_OUTPUT$"):
        await model.answer_customer_service("如何操作？", CONTEXTS)
    assert len(requests) == 1


async def test_provider_rejecting_json_format_does_not_trigger_text_fallback(sdk_model):
    async def reject(_body):
        return httpx.Response(400, json={"error": {"message": "unsupported response_format", "type": "invalid_request_error"}})

    model, requests, _ = sdk_model("json_object", handler=reject)
    with pytest.raises(BadRequestError):
        await model.answer_customer_service("如何操作？", CONTEXTS)
    assert len(requests) == 1
    assert requests[0]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("reason,error", [
    ("length", LengthFinishReasonError), ("content_filter", ContentFilterFinishReasonError),
])
async def test_json_sdk_parse_fails_closed_for_incomplete_finish(sdk_model, reason, error):
    model, requests, _ = sdk_model("json_object", raw=json.dumps(ANSWER), reason=reason)
    with pytest.raises(error):
        await model.answer_customer_service("如何操作？", CONTEXTS)
    assert len(requests) == 1


@pytest.mark.parametrize("reason", ["length", "content_filter"])
async def test_text_incomplete_json_keeps_existing_strict_parse_failure(sdk_model, reason):
    model, requests, _ = sdk_model("text", raw=json.dumps(ANSWER)[:-1], reason=reason)
    with pytest.raises(CustomerServiceModelOutputError):
        await model.answer_customer_service("如何操作？", CONTEXTS)
    assert len(requests) == 1 and "response_format" not in requests[0]


@pytest.mark.parametrize("mode", MODES)
async def test_customer_cancellation_propagates_without_another_send(sdk_model, mode):
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def wait_for_cancellation(_body):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    model, requests, _ = sdk_model(mode, handler=wait_for_cancellation)
    task = asyncio.create_task(model.answer_customer_service("如何操作？", CONTEXTS))
    try:
        await asyncio.wait_for(started.wait(), timeout=5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set() and len(requests) == 1


@pytest.mark.parametrize("mode", MODES)
async def test_customer_budget_rejects_before_http(sdk_model, mode):
    model, requests, _ = sdk_model(mode, model_context_window_tokens=1024,
                                   model_max_tokens=128, generation_max_tokens=128, model_context_overhead_tokens=64)
    with pytest.raises(InputReviewError, match="INPUT_CONTEXT_BUDGET_EXCEEDED"):
        await model.answer_customer_service("问题" * 1500, CONTEXTS)
    assert requests == []


@pytest.mark.parametrize("mode", MODES)
async def test_customer_sdk_call_suppresses_tracing_and_restores_outer_context(sdk_model, mode):
    async def inspect_tracing(_body):
        assert tracing_is_enabled() is False
        return httpx.Response(200, json=completion(json.dumps(ANSWER)))

    model, requests, _ = sdk_model(mode, handler=inspect_tracing)
    with tracing_context(enabled=True):
        await model.answer_customer_service("如何操作？", CONTEXTS)
        assert tracing_is_enabled() is True
    assert len(requests) == 1


async def test_concurrent_customer_and_generation_keep_per_request_format_and_same_client(sdk_model):
    both_started = asyncio.Event()
    entered = []

    async def overlap(body):
        entered.append(body)
        if len(entered) == 2:
            both_started.set()
        await both_started.wait()
        raw = json.dumps(ANSWER) if body["messages"][0]["content"] == CUSTOMER_SERVICE_SYSTEM_PROMPT else "<main>生成</main>"
        return httpx.Response(200, json=completion(raw))

    model, requests, constructors = sdk_model("json_object", handler=overlap)
    client, root = model._client, model._client.root_async_client
    answer, generated = await asyncio.wait_for(asyncio.gather(
        model.answer_customer_service("如何操作？", CONTEXTS),
        model.generate("HTML", {"prompt": "页面"}),
    ), timeout=5)
    assert answer.answered is True and generated.content == "<main>生成</main>"
    customer = next(body for body in requests if body["messages"][0]["content"] == CUSTOMER_SERVICE_SYSTEM_PROMPT)
    generation = next(body for body in requests if body is not customer)
    assert customer["response_format"] == {"type": "json_object"}
    assert "response_format" not in generation
    assert generation["max_completion_tokens"] == 40960
    assert customer["max_completion_tokens"] == 8192
    assert model._client is client and client.root_async_client is root
    assert len(constructors) == 1 and "response_format" not in client.model_kwargs


OTHER_CALLS = [
    ("route", "HTML"),
    *((operation, branch) for operation in ("generate", "repair", "review")
      for branch in ("HTML", "MULTI_FILE", "VUE_PROJECT")),
    *((operation, branch) for operation in ("generate_stream", "repair_stream")
      for branch in ("HTML", "MULTI_FILE")),
    ("review_role", "HTML"), ("review_input", "HTML"), ("rewrite_requirements", "HTML"),
]


@pytest.mark.parametrize("operation,branch", OTHER_CALLS)
async def test_other_adapter_entrypoints_never_send_customer_response_format(sdk_model, operation, branch):
    raw = "<main>页面</main>"
    if operation == "route":
        raw = "HTML"
    elif operation == "review":
        raw = '{"verdict":"PASS","issues":[]}'
    elif operation == "review_role":
        raw = '{"reviewer":"requirement","summary":"通过","issues":[]}'
    elif operation == "review_input":
        raw = '{"decision":"ALLOW","reason":"NONE","message":"","questions":[]}'
    elif operation == "rewrite_requirements":
        raw = json.dumps({"requirements": ["页面"], "preserve_constraints": [], "design_brief": "",
                          "assumptions": [], "interactions": [], "acceptance": []})
    elif branch == "VUE_PROJECT":
        raw = '{"content":"完成","toolCalls":[]}'

    async def respond(body):
        return stream_response(raw) if body.get("stream") else httpx.Response(200, json=completion(raw))

    model, requests, constructors = sdk_model("json_object", handler=respond)
    client = model._client
    context = {"prompt": "页面", "codeGenType": branch}
    if operation == "route":
        await model.route("页面")
    elif operation == "generate":
        await model.generate(branch, context)
    elif operation in {"repair", "review"}:
        await getattr(model, operation)("原产物", context)
    elif operation.endswith("_stream"):
        async def consume(_text):
            pass

        first = branch if operation == "generate_stream" else "原产物"
        await getattr(model, operation)(first, context, on_delta=consume)
    elif operation == "review_role":
        await model.review_role(ReviewerRole.REQUIREMENT, "原产物", context)
    else:
        await getattr(model, operation)(context)
    assert len(requests) == len(constructors) == 1
    assert "response_format" not in requests[0]
    expected_quota = (40960 if operation in {"generate", "repair", "generate_stream", "repair_stream"}
                      else 1024 if operation == "review_input" else 1500 if operation == "rewrite_requirements" else 8192)
    assert requests[0]["max_completion_tokens"] == expected_quota
    assert model._client is client and "response_format" not in client.model_kwargs
