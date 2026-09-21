from __future__ import annotations

import pytest

from ai_service.models.tool_contract import (
    InvalidVueToolCall,
    all_tool_specs,
    validate_vue_tool_call,
    vue_model_tool_specs,
    vue_tool_prompt,
)


MODEL_TOOL_NAMES = {
    "dir_read",
    "file_read",
    "file_write",
    "file_modify",
    "file_delete",
}


def test_contract_exposes_only_supported_file_tools_to_vue_model():
    assert {spec.name for spec in vue_model_tool_specs()} == MODEL_TOOL_NAMES
    assert "search_reference" not in {spec.name for spec in all_tool_specs()}


def test_vue_prompt_lists_canonical_tools_and_keeps_controlled_arguments_internal():
    prompt = vue_tool_prompt()

    assert MODEL_TOOL_NAMES <= {name for name in MODEL_TOOL_NAMES if name in prompt}
    assert "search_reference" not in prompt
    assert "appId" in prompt
    assert "codeGenType" in prompt
    assert "must not provide" in prompt


def test_valid_vue_tool_call_returns_a_defensive_argument_copy():
    arguments = {"relativeFilePath": "src/App.vue"}

    validated = validate_vue_tool_call("file_read", arguments)

    assert validated == arguments
    assert validated is not arguments


@pytest.mark.parametrize("tool_name", ["artifact_validate", "artifact_publish", "project_build"])
def test_internal_workflow_tools_are_rejected_for_model_calls(tool_name: str):
    with pytest.raises(InvalidVueToolCall, match="not available to the Vue model"):
        validate_vue_tool_call(tool_name, {})


def test_unknown_vue_tool_is_rejected():
    with pytest.raises(InvalidVueToolCall, match="Unsupported Vue tool: search_reference"):
        validate_vue_tool_call("search_reference", {})


def test_missing_required_arguments_are_reported():
    with pytest.raises(InvalidVueToolCall, match="missing required arguments: oldContent, newContent"):
        validate_vue_tool_call("file_modify", {"relativeFilePath": "src/App.vue"})


@pytest.mark.parametrize("controlled_name", ["appId", "codeGenType"])
def test_model_cannot_supply_workflow_controlled_arguments(controlled_name: str):
    with pytest.raises(InvalidVueToolCall, match=f"must not provide controlled argument: {controlled_name}"):
        validate_vue_tool_call(
            "file_read",
            {"relativeFilePath": "src/App.vue", controlled_name: "forged"},
        )
