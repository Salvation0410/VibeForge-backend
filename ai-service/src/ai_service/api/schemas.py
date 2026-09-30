from __future__ import annotations

from enum import StrEnum
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ApiModel(BaseModel):
    """内部 API 数据模型基类，统一使用 camelCase JSON 字段。"""
    model_config = ConfigDict(populate_by_name=True, alias_generator=lambda value: _to_camel(value))


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class CodeGenType(StrEnum):
    """服务支持的代码生成分支。"""
    HTML = "HTML"
    MULTI_FILE = "MULTI_FILE"
    VUE_PROJECT = "VUE_PROJECT"


class ChatMessage(ApiModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str


class RouteRequest(ApiModel):
    """代码类型路由请求。"""
    prompt: str = Field(min_length=1, max_length=100_000)
    app_id: str | None = None
    request_id: str | None = None


class RouteResponse(ApiModel):
    """代码类型路由结果。"""
    request_id: str
    code_gen_type: CodeGenType


class GenerationRequest(ApiModel):
    """启动一次 LangGraph 代码生成所需的完整输入。"""
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
    """通过 NDJSON 发送给 Spring 的标准工作流事件。"""
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


class KnowledgeMutationLeaseRequest(ApiModel):
    """Opaque Spring-issued lease. Python validates shape but never creates a substitute."""

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(min_length=1, max_length=256)
    operation_id: str = Field(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"
    )
    operation: Literal["INDEX", "DELETE", "REBUILD"]
    fence: int = Field(ge=1, strict=True)
    expires_at: float = Field(gt=0, allow_inf_nan=False)
    proof: str = Field(min_length=1, max_length=4096, repr=False)

    @model_validator(mode="after")
    def validate_expiry(self) -> "KnowledgeMutationLeaseRequest":
        if not self.proof.strip() or self.expires_at <= time.time():
            raise ValueError("lease is expired")
        return self


class KnowledgeEtlRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["INDEX"]
    document_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    document_version: int = Field(ge=1, strict=True)
    file_name: str = Field(min_length=1, max_length=255)
    file_type: Literal["PDF", "DOCX", "MD", "TXT"]
    signed_url: str = Field(min_length=1, max_length=4096, repr=False)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    etl_version: str = Field(min_length=1, max_length=128)
    lease: KnowledgeMutationLeaseRequest

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        if (
            "://" in value or "?" in value or "#" in value
            or "/" in value or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("invalid file name")
        return value

    @field_validator("sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.lower()

    @field_validator("signed_url")
    @classmethod
    def validate_signed_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError("invalid signed URL") from None
        if (
            parsed.scheme != "https" or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.fragment
            or "\\" in value or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("signed URL must use HTTPS")
        return value

    @model_validator(mode="after")
    def validate_lease_scope(self) -> "KnowledgeEtlRequest":
        if (
            self.lease.scope != f"document:{self.document_id}"
            or self.lease.operation != self.operation
        ):
            raise ValueError("lease scope does not match document")
        return self


class KnowledgeDeleteRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["DELETE"]
    document_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    document_version: int = Field(ge=1, strict=True)
    lease: KnowledgeMutationLeaseRequest

    @model_validator(mode="after")
    def validate_lease_scope(self) -> "KnowledgeDeleteRequest":
        if (
            self.lease.scope != f"document:{self.document_id}"
            or self.lease.operation != self.operation
        ):
            raise ValueError("lease scope does not match document")
        return self


class KnowledgeRebuildDocument(ApiModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    document_version: int = Field(ge=1, strict=True)
    file_name: str = Field(min_length=1, max_length=255)
    file_type: Literal["PDF", "DOCX", "MD", "TXT"]
    signed_url: str = Field(min_length=1, max_length=4096, repr=False)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")

    @field_validator("file_name")
    @classmethod
    def validate_file_name(cls, value: str) -> str:
        return KnowledgeEtlRequest.validate_file_name(value)

    @field_validator("sha256")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        return value.lower()

    @field_validator("signed_url")
    @classmethod
    def validate_signed_url(cls, value: str) -> str:
        return KnowledgeEtlRequest.validate_signed_url(value)


class KnowledgeRebuildRequest(ApiModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["REBUILD"]
    collection_alias: str = Field(
        min_length=1, max_length=255, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"
    )
    documents: list[KnowledgeRebuildDocument] = Field(min_length=1, max_length=10_000)
    etl_version: str = Field(min_length=1, max_length=128)
    lease: KnowledgeMutationLeaseRequest

    @model_validator(mode="after")
    def validate_lease_scope(self) -> "KnowledgeRebuildRequest":
        if (
            self.lease.scope != f"collection:{self.collection_alias}"
            or self.lease.operation != self.operation
        ):
            raise ValueError("lease scope does not match collection")
        if len({item.document_id for item in self.documents}) != len(self.documents):
            raise ValueError("duplicate document id")
        return self


class KnowledgeEtlResponse(ApiModel):
    operation: Literal["INDEX", "DELETE"]
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    document_id: str
    document_version: int
    chunk_count: int = Field(ge=0)
    idempotent: bool = False


class KnowledgeRebuildResponse(ApiModel):
    operation: Literal["REBUILD"]
    status: Literal["SUCCEEDED"] = "SUCCEEDED"
    collection_alias: str
    etl_version: str
    document_count: int = Field(ge=0, le=1_000_000)
    idempotent: bool = False


class StableErrorDetail(ApiModel):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=256)


class StableErrorResponse(ApiModel):
    error: StableErrorDetail


class CustomerServiceHealthResponse(ApiModel):
    enabled: bool
    ready: bool
    dependencies: dict[str, bool]

