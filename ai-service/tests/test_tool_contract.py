from __future__ import annotations

import pytest

from ai_service.models.tool_contract import (
    InvalidVueToolCall,
    ToolContractValidationError,
    _parse_contract,
    all_tool_specs,
    resolve_tool_spec,
    validate_tool_arguments,
    validate_tool_result,
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

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
VALID_CASES = {
    "dir_read": (
        {"relativeDirPath": "", "codeGenType": "VUE_PROJECT"},
        {"entries": ["src/App.vue"]},
    ),
    "file_read": (
        {"relativeFilePath": "src/App.vue", "codeGenType": "VUE_PROJECT"},
        {"content": "<template />"},
    ),
    "file_write": (
        {
            "relativeFilePath": "src/App.vue",
            "content": "<template />",
            "codeGenType": "VUE_PROJECT",
        },
        {"ok": True, "path": "App.vue"},
    ),
    "file_modify": (
        {
            "relativeFilePath": "src/App.vue",
            "oldContent": "before",
            "newContent": "after",
            "codeGenType": "VUE_PROJECT",
        },
        {"ok": False, "message": "oldContent not found"},
    ),
    "file_delete": (
        {"relativeFilePath": "src/Old.vue", "codeGenType": "VUE_PROJECT"},
        {"ok": True},
    ),
    "artifact_context": (
        {"codeGenType": "HTML"},
        {
            "exists": True,
            "codeGenType": "HTML",
            "entry": "index.html",
            "artifact": "```html\nready\n```",
        },
    ),
    "artifact_validate": (
        {"artifact": "candidate", "codeGenType": "HTML"},
        {
            "valid": False,
            "errors": [
                {
                    "code": "HTML_FORMAT_INVALID",
                    "message": "invalid",
                    "file": "index.html",
                }
            ],
        },
    ),
    "artifact_publish": (
        {
            "artifact": "candidate",
            "codeGenType": "MULTI_FILE",
            "engine": "langgraph",
            "finishReason": "STOP",
        },
        {
            "published": True,
            "versionId": "release-1",
            "hashes": {"index.html": "abc"},
        },
    ),
    "project_build": (
        {"codeGenType": "VUE_PROJECT"},
        {
            "built": False,
            "errorCode": "VUE_NPM_BUILD_FAILED",
            "message": "vite failed",
        },
    ),
    "vue_source_snapshot": (
        {"codeGenType": "VUE_PROJECT"},
        {
            "files": [{"path": "src/App.vue", "content": "<template />", "truncated": False}],
            "eligibleFileCount": 1,
            "includedFileCount": 1,
            "omittedFileCount": 0,
            "truncated": False,
        },
    ),
}


@pytest.mark.parametrize(("name", "case"), VALID_CASES.items())
def test_all_tool_request_and_response_examples_match_contract(name, case):
    arguments, result = case

    assert validate_tool_arguments(name, arguments) is arguments
    assert validate_tool_result(name, result) is result


@pytest.mark.parametrize(
    "result",
    [
        {"exists": False, "codeGenType": "HTML"},
        {"exists": True, "codeGenType": "HTML", "artifact": "<html />"},
        {"exists": True, "codeGenType": "MULTI_FILE", "artifact": "files"},
        {
            "exists": True,
            "codeGenType": "VUE_PROJECT",
            "entries": ["src/App.vue"],
            "truncated": False,
        },
    ],
)
def test_artifact_context_response_shapes_match_contract(result):
    assert validate_tool_result("artifact_context", result) is result


@pytest.mark.parametrize(
    "arguments",
    [
        {"codeGenType": "VUE_PROJECT"},
        {"relativeFilePath": 42, "codeGenType": "VUE_PROJECT"},
        {
            "relativeFilePath": "src/App.vue",
            "codeGenType": "VUE_PROJECT",
            "appId": "forged",
        },
        {"relativeFilePath": "src/App.vue", "codeGenType": "vue_project"},
    ],
)
def test_request_rejects_missing_wrong_typed_additional_and_nonstandard_fields(arguments):
    with pytest.raises(
        ToolContractValidationError,
        match="Internal AI tool payload did not match expected schema",
    ) as exc_info:
        validate_tool_arguments("file_read", arguments)

    assert "src/App.vue" not in str(exc_info.value)
    assert exc_info.value.__cause__ is not None


def test_response_allows_new_fields_but_checks_declared_fields():
    result = {"built": True, "errorCode": "", "message": "", "durationMs": 125}
    assert validate_tool_result("project_build", result) is result

    with pytest.raises(ToolContractValidationError) as exc_info:
        validate_tool_result(
            "project_build",
            {"built": "sensitive-instance-value", "errorCode": "", "message": ""},
        )

    assert "sensitive-instance-value" not in str(exc_info.value)
    assert exc_info.value.__cause__ is not None


def test_nested_response_fields_remain_typed_while_allowing_extensions():
    result = {
        "valid": False,
        "errors": [{"code": "INVALID", "message": "bad", "severity": "error"}],
        "checkedAt": "now",
    }
    assert validate_tool_result("artifact_validate", result) is result

    with pytest.raises(ToolContractValidationError):
        validate_tool_result(
            "artifact_publish",
            {"published": True, "versionId": "v1", "hashes": {"index.html": 42}},
        )


def test_alias_uses_canonical_schema_and_unknown_tool_fails():
    assert resolve_tool_spec("readFile").name == "file_read"
    arguments = {"relativeFilePath": "index.html", "codeGenType": "HTML"}
    assert validate_tool_arguments("readFile", arguments) is arguments

    with pytest.raises(ToolContractValidationError, match="Unknown internal AI tool"):
        validate_tool_arguments("search_reference", {})


def _valid_contract_payload() -> dict:
    object_schema = {"type": "object"}
    return {
        "version": 1,
        "schemaDialect": SCHEMA_DIALECT,
        "tools": [
            {
                "name": "file_read",
                "aliases": ["readFile"],
                "modelCallable": True,
                "modelArguments": ["relativeFilePath"],
                "description": "Read a file.",
                "requestSchema": object_schema.copy(),
                "responseSchema": object_schema.copy(),
            }
        ],
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(version=2),
        lambda payload: payload.update(schemaDialect="https://json-schema.org/draft/2019-09/schema"),
        lambda payload: payload["tools"][0].pop("requestSchema"),
        lambda payload: payload["tools"][0].pop("responseSchema"),
        lambda payload: payload["tools"].append(payload["tools"][0].copy()),
        lambda payload: payload["tools"].append(
            {
                **payload["tools"][0],
                "name": "file_write",
                "aliases": ["file_read"],
            }
        ),
        lambda payload: payload["tools"][0].update(requestSchema={"type": "invalid"}),
    ],
    ids=[
        "version",
        "dialect",
        "missing-request-schema",
        "missing-response-schema",
        "duplicate-canonical-name",
        "alias-canonical-conflict",
        "invalid-schema",
    ],
)
def test_malformed_contract_is_rejected(mutate):
    payload = _valid_contract_payload()
    mutate(payload)

    with pytest.raises(RuntimeError):
        _parse_contract(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", 42),
        ("aliases", "readFile"),
        ("aliases", [42]),
        ("modelCallable", "true"),
        ("modelArguments", "relativeFilePath"),
        ("modelArguments", [42]),
        ("description", 42),
        ("requestSchema", []),
        ("responseSchema", []),
    ],
)
def test_contract_metadata_with_wrong_types_is_rejected(field, value):
    payload = _valid_contract_payload()
    payload["tools"][0][field] = value

    with pytest.raises(RuntimeError, match="malformed"):
        _parse_contract(payload)


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


@pytest.mark.parametrize(
    "tool_name",
    [
        "artifact_context",
        "artifact_validate",
        "artifact_publish",
        "project_build",
        "vue_source_snapshot",
    ],
)
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


@pytest.mark.parametrize("code_gen_type", ["HTML", "MULTI_FILE", "vue_project"])
def test_vue_source_snapshot_rejects_non_vue_project_code_gen_type(code_gen_type: str):
    with pytest.raises(ToolContractValidationError):
        validate_tool_arguments("vue_source_snapshot", {"codeGenType": code_gen_type})
