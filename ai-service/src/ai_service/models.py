from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, alias_generator=lambda value: _to_camel(value))


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class CodeGenType(StrEnum):
    HTML = "HTML"
    MULTI_FILE = "MULTI_FILE"
    VUE_PROJECT = "VUE_PROJECT"


class ChatMessage(ApiModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class RouteRequest(ApiModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    app_id: str | None = None
    request_id: str | None = None


class RouteResponse(ApiModel):
    request_id: str
    code_gen_type: CodeGenType


class GenerationRequest(ApiModel):
    request_id: str = Field(min_length=1, max_length=128)
    app_id: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=100_000)
    code_gen_type: CodeGenType
    conversation: list[ChatMessage] = Field(default_factory=list, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CancelRequest(ApiModel):
    app_id: str = Field(min_length=1, max_length=128)


class CancelResponse(ApiModel):
    request_id: str
    status: Literal["cancelled"] = "cancelled"


class EventError(ApiModel):
    code: str
    message: str


class GenerationEvent(ApiModel):
    type: Literal[
        "content_delta",
        "tool_started",
        "tool_finished",
        "node_status",
        "completed",
        "failed",
    ]
    request_id: str
    sequence: int = Field(ge=1)
    node: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: EventError | None = None

