from __future__ import annotations

import json
import traceback
from types import SimpleNamespace

import pytest

from ai_service.config import Settings
from ai_service.models import openai_compatible
from ai_service.models.openai_compatible import OpenAICompatibleModel
from ai_service.models.quality_review import (
    QualityReviewOutputError,
    ReviewerRole,
)
from ai_service.models.tool_contract import vue_tool_prompt
from ai_service.prompts import (
    QUALITY_REVIEW_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    ROUTING_SYSTEM_PROMPT,
    generation_system_prompt,
    quality_review_system_prompt,
)


class CapturingClient:
    def __init__(self, response_content='{"content":"done","toolCalls":[]}'):
        self.messages = None
        self.response_content = response_content

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(
            content=self.response_content,
            response_metadata={},
            usage_metadata={},
        )


def model_with_response(response_content: str) -> OpenAICompatibleModel:
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model._client = CapturingClient(response_content)
    return model


def test_model_client_receives_configured_max_tokens(monkeypatch):
    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return CapturingClient()

    monkeypatch.setattr(openai_compatible, "ChatOpenAI", fake_chat_openai)
    settings = Settings(
        internal_bearer_token="internal-token",
        spring_gateway_base_url="http://spring.test",
        spring_gateway_bearer_token="gateway-token",
        model_max_tokens=4096,
    )

    OpenAICompatibleModel(settings)

    assert captured["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_route_uses_routing_system_prompt():
    model = model_with_response("html")

    assert await model.route("做一个官网") == "HTML"
    assert model._client.messages[0].content == ROUTING_SYSTEM_PROMPT


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE"])
async def test_static_generation_uses_branch_system_prompt(branch):
    model = model_with_response("artifact")

    await model.generate(branch, {"prompt": "build it"})

    assert model._client.messages[0].content == generation_system_prompt(branch)


@pytest.mark.asyncio
async def test_vue_generation_uses_the_versioned_tool_prompt():
    model = model_with_response('{"content":"done","toolCalls":[]}')

    await model.generate("VUE_PROJECT", {"prompt": "build it"})

    system_prompt = str(model._client.messages[0].content)
    assert generation_system_prompt("VUE_PROJECT") in system_prompt
    assert vue_tool_prompt() in system_prompt
    assert "file_read" in system_prompt
    assert "file_write" in system_prompt
    assert "search_reference" not in system_prompt


@pytest.mark.asyncio
async def test_vue_generation_falls_back_when_json_top_level_is_not_an_object():
    model = model_with_response("[]")

    result = await model.generate("VUE_PROJECT", {"prompt": "build it"})

    assert result.content == "[]"
    assert result.tool_calls == []


@pytest.mark.asyncio
async def test_non_vue_generation_does_not_include_vue_tool_instructions():
    model = model_with_response("artifact")

    await model.generate("HTML", {"prompt": "build it"})

    system_prompt = str(model._client.messages[0].content)
    assert "file_read" not in system_prompt
    assert "toolCalls" not in system_prompt


@pytest.mark.asyncio
async def test_review_and_repair_use_their_system_prompts():
    review_model = model_with_response("PASS")

    assert await review_model.review("artifact", {"prompt": "check"}) is True
    assert review_model._client.messages[0].content == QUALITY_REVIEW_SYSTEM_PROMPT

    repair_model = model_with_response("fixed")

    result = await repair_model.repair("artifact", {"validation": {"valid": False}})
    assert result.content == "fixed"
    assert repair_model._client.messages[0].content == REPAIR_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_role_review_parses_valid_json_and_preserves_human_payload_contract():
    model = model_with_response(
        '{"reviewer":"requirement","summary":"完成","issues":[]}'
    )

    result = await model.review_role(
        ReviewerRole.REQUIREMENT,
        "artifact",
        {"prompt": "check", "buildSummary": "passed"},
    )

    assert result.reviewer is ReviewerRole.REQUIREMENT
    assert result.summary == "完成"
    assert model._client.messages[0].content == quality_review_system_prompt(
        ReviewerRole.REQUIREMENT.value
    )
    assert json.loads(model._client.messages[1].content) == {
        "artifact": "artifact",
        "prompt": "check",
        "buildSummary": "passed",
    }


@pytest.mark.asyncio
async def test_role_review_accepts_markdown_json_fence():
    model = model_with_response(
        '```json\n{"reviewer":"function","summary":"完成","issues":[]}\n```'
    )

    result = await model.review_role(ReviewerRole.FUNCTION, "artifact", {})

    assert result.reviewer is ReviewerRole.FUNCTION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_content",
    [
        '{"reviewer":"technical","summary":"完成","issues":[],"extra":true}',
        '{"reviewer":"technical","summary":"' + "x" * 301 + '","issues":[]}',
        (
            '{"reviewer":"technical","summary":"完成","issues":['
            '{"code":"X","category":"logic","summary":"problem",'
            '"evidence":"evidence","repair_hint":"repair",'
            '"severity":"unknown"}]}'
        ),
    ],
)
async def test_role_review_rejects_schema_violations(response_content):
    model = model_with_response(response_content)

    with pytest.raises(QualityReviewOutputError) as error:
        await model.review_role(ReviewerRole.TECHNICAL, "artifact", {})

    assert str(error.value) == "MULTI_AGENT_REVIEW_INVALID_OUTPUT: invalid JSON"
    assert response_content not in str(error.value)


@pytest.mark.asyncio
async def test_role_review_rejects_invalid_json_without_leaking_raw_content():
    marker = "TRACEBACK_SECRET_SOURCE_MARKER"
    raw = f'{{"privateSource":"<script>{marker}</script>"'
    model = model_with_response(raw)

    with pytest.raises(QualityReviewOutputError) as error:
        await model.review_role(ReviewerRole.REQUIREMENT, "artifact", {})

    assert str(error.value) == "MULTI_AGENT_REVIEW_INVALID_OUTPUT: invalid JSON"
    assert error.value.__cause__ is None
    assert raw not in str(error.value)
    formatted_traceback = "".join(
        traceback.format_exception(error.type, error.value, error.tb)
    )
    assert marker not in formatted_traceback


@pytest.mark.asyncio
async def test_role_review_rejects_reviewer_identity_mismatch():
    model = model_with_response(
        '{"reviewer":"function","summary":"完成","issues":[]}'
    )

    with pytest.raises(QualityReviewOutputError) as error:
        await model.review_role(ReviewerRole.REQUIREMENT, "artifact", {})

    assert str(error.value) == (
        "MULTI_AGENT_REVIEW_INVALID_OUTPUT: reviewer identity mismatch"
    )


@pytest.mark.asyncio
async def test_vue_repair_uses_tool_prompt_and_parses_tool_calls():
    model = model_with_response(
        '{"content":"inspected","toolCalls":['
        '{"name":"file_read","arguments":{"relativeFilePath":"src/App.vue"}}]}'
    )

    result = await model.repair(
        "existing artifact",
        {"codeGenType": "VUE_PROJECT", "repairCount": 1},
    )

    system_prompt = str(model._client.messages[0].content)
    assert REPAIR_SYSTEM_PROMPT in system_prompt
    assert vue_tool_prompt() in system_prompt
    assert result.content == "inspected"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "file_read"
    assert result.tool_calls[0].arguments == {"relativeFilePath": "src/App.vue"}


@pytest.mark.asyncio
async def test_static_repair_remains_plain_text_with_strict_repair_prompt():
    model = model_with_response('{"content":"fixed","toolCalls":[]}')

    result = await model.repair(
        "existing artifact",
        {"codeGenType": "HTML", "repairCount": 1},
    )

    assert model._client.messages[0].content == REPAIR_SYSTEM_PROMPT
    assert result.content == '{"content":"fixed","toolCalls":[]}'
    assert result.tool_calls == []
