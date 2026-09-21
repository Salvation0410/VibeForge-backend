from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any


_CONTRACT_PACKAGE = "ai_service.contracts"
_CONTRACT_FILE = "internal-ai-tools-v1.json"
_CONTROLLED_ARGUMENTS = ("appId", "codeGenType")


class InvalidVueToolCall(ValueError):
    """The model requested a tool call outside the Spring-owned contract."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    aliases: tuple[str, ...]
    model_callable: bool
    model_arguments: tuple[str, ...]
    description: str


@lru_cache(maxsize=1)
def all_tool_specs() -> tuple[ToolSpec, ...]:
    """Load the packaged cross-service tool contract once per process."""
    contract_path = files(_CONTRACT_PACKAGE).joinpath(_CONTRACT_FILE)
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise RuntimeError("Unsupported internal AI tool contract version")
    specs = tuple(
        ToolSpec(
            name=item["name"],
            aliases=tuple(item.get("aliases", [])),
            model_callable=bool(item["modelCallable"]),
            model_arguments=tuple(item.get("modelArguments", [])),
            description=item["description"],
        )
        for item in payload.get("tools", [])
    )
    names = [spec.name for spec in specs]
    if not specs or len(names) != len(set(names)):
        raise RuntimeError("Internal AI tool contract contains missing or duplicate names")
    return specs


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
    specs = {spec.name: spec for spec in all_tool_specs()}
    spec = specs.get(name)
    if spec is None:
        raise InvalidVueToolCall(f"Unsupported Vue tool: {name}")
    if not spec.model_callable:
        raise InvalidVueToolCall(f"Tool {name} is not available to the Vue model")
    if not isinstance(arguments, dict):
        raise InvalidVueToolCall(f"Tool {name} arguments must be an object")
    for controlled_name in _CONTROLLED_ARGUMENTS:
        if controlled_name in arguments:
            raise InvalidVueToolCall(
                f"Tool {name} must not provide controlled argument: {controlled_name}"
            )
    missing = [key for key in spec.model_arguments if key not in arguments]
    if missing:
        raise InvalidVueToolCall(
            f"Tool {name} is missing required arguments: {', '.join(missing)}"
        )
    unexpected = [key for key in arguments if key not in spec.model_arguments]
    if unexpected:
        raise InvalidVueToolCall(
            f"Tool {name} has unsupported arguments: {', '.join(unexpected)}"
        )
    return dict(arguments)
