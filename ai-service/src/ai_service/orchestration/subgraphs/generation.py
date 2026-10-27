"""构建静态生成分支子图；Vue 多节点工具图在 vue.py 中组装。"""

from collections.abc import Callable

from langgraph.graph import END, START, StateGraph

from ai_service.orchestration.subgraphs.schemas import (
    GenerationInput,
    StaticGenerationOutput,
    StaticGenerationState,
)


def build_generation_subgraph(node_name: str, node: Callable):
    """保留节点事件名称；不传 saver，由父图提供请求级持久化。"""
    builder = StateGraph(StaticGenerationState, input_schema=GenerationInput, output_schema=StaticGenerationOutput)
    builder.add_node(node_name, node, input_schema=StaticGenerationState)
    builder.add_edge(START, node_name)
    builder.add_edge(node_name, END)
    return builder.compile()
