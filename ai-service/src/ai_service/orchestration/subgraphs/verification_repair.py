"""封装校验、构建、质量审查与有限修复，不负责发布或公共终态。"""

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ai_service.orchestration.routing import _after_build, _after_review, _after_validation
from ai_service.orchestration.state import WorkflowState


class VerificationOutput(TypedDict, total=False):
    """传回经验证的候选、修复上下文和跨阶段预算。"""

    context: dict[str, Any]
    artifact: str
    validation: dict[str, Any]
    build: dict[str, Any]
    quality_passed: bool
    repair_feedback: str
    repair_count: int
    tool_call_count: int
    finish_reason: str | None
    token_usage: dict[str, int]


def build_verification_repair_subgraph(
    nodes: Mapping[str, Callable],
    max_repair_attempts: int,
    *,
    interrupt_after: Sequence[str] | None = None,
):
    """调试断点属于子图节点；父图负责 saver 和命名空间，生产默认不中断。"""
    builder = StateGraph(WorkflowState, output_schema=VerificationOutput)
    for name, node in nodes.items():
        builder.add_node(name, node)
    builder.add_edge(START, "artifact_validation")
    builder.add_conditional_edges(
        "artifact_validation",
        lambda state: _after_validation(state, max_repair_attempts),
        {"repair": "repair", "fail": "fail_quality", "build": "project_build", "review": "quality_review"},
    )
    builder.add_conditional_edges(
        "project_build",
        lambda state: _after_build(state, max_repair_attempts),
        {"repair": "repair", "fail": "fail_quality", "review": "quality_review"},
    )
    builder.add_conditional_edges(
        "quality_review",
        lambda state: _after_review(state, max_repair_attempts),
        {"repair": "repair", "fail": "fail_quality", "publish": END, "finalize": END},
    )
    builder.add_edge("repair", "artifact_validation")
    return builder.compile(interrupt_after=list(interrupt_after) if interrupt_after else None)
