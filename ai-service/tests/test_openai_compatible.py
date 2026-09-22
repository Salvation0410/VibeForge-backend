from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_service.models.openai_compatible import OpenAICompatibleModel
from ai_service.models.tool_contract import vue_tool_prompt
from ai_service.prompts import (
    QUALITY_REVIEW_SYSTEM_PROMPT,
    REPAIR_SYSTEM_PROMPT,
    ROUTING_SYSTEM_PROMPT,
    generation_system_prompt,
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
