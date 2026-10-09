from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from ai_service.models.quality_review import ReviewerResult, ReviewerRole
from ai_service.models.input_review import InputReviewResult


@dataclass(slots=True)
class ToolCall:
    """模型请求执行的一次外部工具调用。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelTurn:
    """一次模型响应，包含文本产物、工具调用和可用于阻止截断发布的结束元数据。"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)
    file_plan: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CustomerServiceContext:
    """A bounded, untrusted knowledge fragment presented to the answer model."""

    chunk_id: str
    content: str


@dataclass(frozen=True, slots=True)
class CustomerServiceModelAnswer:
    """Strict answer contract returned by the customer-service model adapter."""

    answered: bool
    answer: str
    cited_chunk_ids: tuple[str, ...]


class GenerationModel(Protocol):
    """编排层依赖的模型能力协议，用于隔离具体模型供应商。"""

    async def review_input(self, context: dict[str, Any]) -> InputReviewResult:
        """返回严格输入审核决策，不改写用户原始需求。"""
        ...

    async def route(self, prompt: str) -> str:
        """将用户需求分类为服务支持的代码生成类型。"""
        ...

    async def generate(self, branch: str, context: dict[str, Any]) -> ModelTurn:
        """根据生成分支和上下文生成产物或工具调用。"""
        ...

    async def review(self, artifact: str, context: dict[str, Any]) -> bool:
        """检查生成产物是否达到结束工作流的质量要求。"""
        ...

    async def review_role(
        self,
        role: ReviewerRole,
        artifact: str,
        context: dict[str, Any],
    ) -> ReviewerResult:
        """以指定审查角色返回严格校验的结构化质量结论。"""
        ...

    async def repair(self, artifact: str, context: dict[str, Any]) -> ModelTurn:
        """根据验证与构建上下文修复产物，并保留模型结束元数据。"""
        ...

    async def answer_customer_service(
        self,
        question: str,
        contexts: list[CustomerServiceContext],
    ) -> CustomerServiceModelAnswer:
        """Answer one question using only the supplied knowledge fragments."""
        ...
