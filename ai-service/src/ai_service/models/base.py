from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ToolCall:
    """模型请求执行的一次外部工具调用。"""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelTurn:
    """一次模型响应，包含文本产物和可选工具调用。"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    token_usage: dict[str, int] = field(default_factory=dict)


class GenerationModel(Protocol):
    """编排层依赖的模型能力协议，用于隔离具体模型供应商。"""

    async def route(self, prompt: str) -> str:
        """将用户需求分类为服务支持的代码生成类型。"""
        ...

    async def generate(self, branch: str, context: dict[str, Any]) -> ModelTurn:
        """根据生成分支和上下文生成产物或工具调用。"""
        ...

    async def review(self, artifact: str, context: dict[str, Any]) -> bool:
        """检查生成产物是否达到结束工作流的质量要求。"""
        ...

    async def repair(self, artifact: str, context: dict[str, Any]) -> ModelTurn:
        """根据验证与构建上下文修复产物，并保留模型结束元数据。"""
        ...
