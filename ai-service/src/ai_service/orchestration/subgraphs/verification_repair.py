"""封装校验、构建、质量审查与有限修复，不负责发布或公共终态。"""

from collections.abc import Callable, Mapping, Sequence

from langgraph.graph import END, START, StateGraph

from ai_service.orchestration.routing import _after_build, _after_review, _after_validation
from ai_service.orchestration.subgraphs.schemas import (
    RepairInput, VerificationInput, VerificationOutput, VerificationState,
)


def build_verification_repair_subgraph(
    nodes: Mapping[str, Callable],
    max_repair_attempts: int,
    *,
    interrupt_after: Sequence[str] | None = None,
):
    """调试断点属于子图节点；父图负责 saver 和命名空间，生产默认不中断。"""
    builder = StateGraph(VerificationState, input_schema=VerificationInput, output_schema=VerificationOutput)
    for name, node in nodes.items():
        builder.add_node(name, node, input_schema=RepairInput if name == "repair" else VerificationState)
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
