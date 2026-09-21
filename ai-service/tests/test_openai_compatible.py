from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_service.models.openai_compatible import OpenAICompatibleModel


class CapturingClient:
    def __init__(self):
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content='{"content":"done","toolCalls":[]}', response_metadata={}, usage_metadata={})


@pytest.mark.asyncio
async def test_vue_generation_uses_the_versioned_tool_prompt():
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model._client = CapturingClient()

    await model.generate("VUE_PROJECT", {"prompt": "build it"})

    system_prompt = str(model._client.messages[0].content)
    assert "file_read" in system_prompt
    assert "file_write" in system_prompt
    assert "search_reference" not in system_prompt


@pytest.mark.asyncio
async def test_non_vue_generation_does_not_include_vue_tool_instructions():
    model = OpenAICompatibleModel.__new__(OpenAICompatibleModel)
    model._client = CapturingClient()

    await model.generate("HTML", {"prompt": "build it"})

    system_prompt = str(model._client.messages[0].content)
    assert "file_read" not in system_prompt
    assert "toolCalls" not in system_prompt
