from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError


_CONTRACT_PACKAGE = "ai_service.contracts"
_CONTRACT_FILE = "internal-ai-tools-v1.json"
_CONTROLLED_ARGUMENTS = ("appId", "codeGenType")
_DIALECT = "https://json-schema.org/draft/2020-12/schema"


class InvalidVueToolCall(ValueError):
    """The model requested a tool call outside the Spring-owned contract."""


class ToolContractValidationError(ValueError):
    """An internal tool name, request, or result violates the shared contract."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    aliases: tuple[str, ...]
    model_callable: bool
    model_arguments: tuple[str, ...]
    description: str
    request_schema: dict[str, Any]
    response_schema: dict[str, Any]


def _parse_contract(payload: dict[str, Any]) -> tuple[ToolSpec, ...]:
    if not isinstance(payload, dict):
        raise RuntimeError("Internal AI tool contract is malformed")
    if type(payload.get("version")) is not int or payload["version"] != 1:
        raise RuntimeError("Unsupported internal AI tool contract version")
    if payload.get("schemaDialect") != _DIALECT:
        raise RuntimeError("Unsupported internal AI tool schema dialect")
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        raise RuntimeError("Internal AI tool contract is malformed")
    try:
        specs = tuple(_parse_tool_spec(item) for item in tools)
    except (KeyError, TypeError) as error:
        raise RuntimeError("Internal AI tool contract is malformed") from error
    external_names = [value for spec in specs for value in (spec.name, *spec.aliases)]
    if not specs or len(external_names) != len(set(external_names)):
        raise RuntimeError("Internal AI tool contract contains missing or duplicate names")
    try:
        for spec in specs:
            Draft202012Validator.check_schema(spec.request_schema)
            Draft202012Validator.check_schema(spec.response_schema)
    except SchemaError as error:
        raise RuntimeError("Internal AI tool contract contains an invalid schema") from error
    return specs


def _parse_tool_spec(item: Any) -> ToolSpec:
    if not isinstance(item, dict):
        raise RuntimeError("Internal AI tool contract is malformed")
    name = item["name"]
    aliases = item.get("aliases", [])
    model_callable = item["modelCallable"]
    model_arguments = item.get("modelArguments", [])
    description = item["description"]
    request_schema = item["requestSchema"]
    response_schema = item["responseSchema"]
    if (
        not isinstance(name, str)
        or not name
        or not isinstance(aliases, list)
        or any(not isinstance(alias, str) or not alias for alias in aliases)
        or type(model_callable) is not bool
        or not isinstance(model_arguments, list)
        or any(not isinstance(argument, str) or not argument for argument in model_arguments)
        or not isinstance(description, str)
        or not isinstance(request_schema, dict)
        or not isinstance(response_schema, dict)
    ):
        raise RuntimeError("Internal AI tool contract is malformed")
    return ToolSpec(
        name=name,
        aliases=tuple(aliases),
        model_callable=model_callable,
        model_arguments=tuple(model_arguments),
        description=description,
        request_schema=request_schema,
        response_schema=response_schema,
    )


@lru_cache(maxsize=1)
def all_tool_specs() -> tuple[ToolSpec, ...]:
    """Load the packaged cross-service tool contract once per process."""
    contract_path = files(_CONTRACT_PACKAGE).joinpath(_CONTRACT_FILE)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    return _parse_contract(payload)


def resolve_tool_spec(name: str) -> ToolSpec:
    """Resolve a canonical tool name or a supported historical alias."""
    for spec in all_tool_specs():
        if name == spec.name or name in spec.aliases:
            return spec
    raise ToolContractValidationError("Unknown internal AI tool")


def _validate(name: str, value: dict[str, Any], *, response: bool) -> dict[str, Any]:
    spec = resolve_tool_spec(name)
    schema = spec.response_schema if response else spec.request_schema
    try:
        Draft202012Validator(schema).validate(value)
    except ValidationError as error:
        raise ToolContractValidationError(
            "Internal AI tool payload did not match expected schema"
        ) from error
    return value


def validate_tool_arguments(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate complete workflow-owned arguments without modifying them."""
    return _validate(name, arguments, response=False)


def validate_tool_result(name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Validate a successful Spring tool result without modifying it."""
    return _validate(name, result, response=True)


def vue_model_tool_specs() -> tuple[ToolSpec, ...]:
    """Return only canonical tools that a Vue model may request directly."""
    return tuple(spec for spec in all_tool_specs() if spec.model_callable)


def vue_tool_prompt() -> str:
    """Render the strict JSON tool instructions from the versioned contract."""
    lines = [
        "For VUE_PROJECT, you may request only these Spring-owned tools:",
        *(
            f"- {spec.name}({', '.join(spec.model_arguments)}): {spec.description}"
            for spec in vue_model_tool_specs()
        ),
        "appId and codeGenType are controlled by the workflow; you must not provide them.",
        "Return strict JSON in this shape: "
        '{"content":"...","toolCalls":[{"name":"file_read","arguments":{"relativeFilePath":"src/App.vue"}}]}.',
        "When no tool is needed, return the artifact as content with an empty toolCalls list.",
    ]
    return "\n".join(lines)


def validate_vue_tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate a model-selected tool before trusted workflow fields are injected."""
    try:
        spec = resolve_tool_spec(name)
    except ToolContractValidationError:
        raise _invalid_call(f"Unsupported Vue tool: {name}")
    if name != spec.name:
        raise _invalid_call(f"Unsupported Vue tool: {name}")
    if not spec.model_callable:
        raise _invalid_call(f"Tool {name} is not available to the Vue model")
    if not isinstance(arguments, dict):
        raise _invalid_call(f"Tool {name} arguments must be an object")
    for controlled_name in _CONTROLLED_ARGUMENTS:
        if controlled_name in arguments:
            raise _invalid_call(f"Tool {name} must not provide controlled argument: {controlled_name}")
    missing = [key for key in spec.model_arguments if key not in arguments]
    if missing:
        raise _invalid_call(f"Tool {name} is missing required arguments: {', '.join(missing)}")
    unexpected = [key for key in arguments if key not in spec.model_arguments]
    if unexpected:
        raise _invalid_call(f"Tool {name} has unsupported arguments: {', '.join(unexpected)}")
    return dict(arguments)


def _invalid_call(message: str) -> InvalidVueToolCall:
    return InvalidVueToolCall(f"INVALID_VUE_TOOL_CALL: {message}")
