"""构建生成分支子图；文件工具循环仍在 Vue 节点内部运行。"""

from collections.abc import Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ai_service.orchestration.state import WorkflowState


class GenerationOutput(TypedDict, total=False):
    """仅将生成阶段负责的字段回传父图，避免覆盖请求身份和其他阶段结果。"""

    context: dict[str, Any]
    artifact: str
    tool_call_count: int
    finish_reason: str | None
    token_usage: dict[str, int]


def build_generation_subgraph(node_name: str, node: Callable):
    """保留节点事件名称；不传 saver，由父图提供请求级持久化。"""
    builder = StateGraph(WorkflowState, output_schema=GenerationOutput)
    builder.add_node(node_name, node)
    builder.add_edge(START, node_name)
    builder.add_edge(node_name, END)
    return builder.compile()
