"""生成和修复共用的多节点 Vue 工具子图，不创建独立 saver 或运行时客户端。"""

from datetime import UTC, datetime

from langgraph.graph import END, START, StateGraph

from ai_service.orchestration.nodes.common import complete_node, start_node
from ai_service.orchestration.state import WorkflowState
from ai_service.orchestration.subgraphs.generation import GenerationOutput
from ai_service.orchestration.subgraphs.verification_repair import VerificationOutput
from ai_service.orchestration.vue_tool_loop import VueLoopState, create_vue_loop_nodes, initial_progress


def vue_recursion_limit(settings) -> int:
    """每工具最多四步，额外容纳有限规划/图片/截断重试和父图修复调度。"""
    return (settings.vue_max_tool_calls * 4 + 64) * (settings.max_repair_attempts + 1) + 32


def build_vue_tool_subgraph(workflow, emitter, thread_id, terminal, *, node="vue_agent",
                            interrupt_after=None, invoke_model=None, call_id_prefix=None, legacy=False):
    """私有轮次状态留在子图命名空间；调试断点不在生产入口开放。"""
    async def initialize(state: WorkflowState):
        await start_node(workflow, emitter, thread_id, terminal, node, state)
        context = {**state["context"], "toolResults": list(state["context"].get("toolResults", []))}
        count = state.get("repair_count", 0) + (node == "repair" and not legacy)
        if node == "repair" and not legacy:
            context.update(codeGenType=state["code_gen_type"], repairCount=count,
                           validation=state.get("validation"), build=state.get("build"))
            if state.get("repair_feedback"):
                context["qualityReview"] = {"repairFeedback": state["repair_feedback"]}
        prefix = call_id_prefix or (f"{state['request_id']}:vue-repair:{count}" if node == "repair"
                                    else f"{state['request_id']}:vue-generate")
        return {
            "context": context, "repair_count": count, "token_usage": {}, "finish_reason": None,
            "vue_loop": initial_progress(context, prefix, datetime.now(UTC).isoformat()),
        }

    async def finish(state: VueLoopState):
        workflow._raise_if_cancelled(thread_id)
        update = {
            "context": state["context"], "tool_call_count": state.get("tool_call_count", 0),
            "finish_reason": state.get("finish_reason"), "token_usage": state.get("token_usage", {}),
        }
        if node == "repair" and not legacy:
            update.update(context={key: value for key, value in state["context"].items() if key != "qualityReview"},
                          build={}, repair_count=state["repair_count"], repair_feedback="")
        else:
            update["artifact"] = "\n".join(state["vue_loop"]["content_parts"])
        await complete_node(workflow, emitter, thread_id, terminal, node, {**state, **update},
                            datetime.fromisoformat(state["vue_loop"]["started_at"]))
        return update

    # 仅阶段输出回传父图；vue_loop、待执行参数和游标均不泄漏。
    builder = StateGraph(VueLoopState, input_schema=WorkflowState,
                         output_schema=VerificationOutput if node == "repair" and not legacy else GenerationOutput)
    builder.add_node("initialize", initialize)
    for name, action in create_vue_loop_nodes(workflow, emitter, thread_id, node, invoke_model=invoke_model).items():
        builder.add_node(name, action)
    builder.add_node("finish", finish)
    builder.add_edge(START, "initialize")
    builder.add_edge("initialize", "model_turn")
    builder.add_edge("model_turn", "validate_turn")
    destinations = {"execute_tool": "execute_tool", "model_turn": "model_turn", "finish": "finish"}
    builder.add_conditional_edges("validate_turn", lambda state: state["vue_loop"]["next_step"], destinations)
    builder.add_edge("execute_tool", "continue_loop")
    builder.add_conditional_edges("continue_loop", lambda state: state["vue_loop"]["next_step"], destinations)
    builder.add_edge("finish", END)
    return builder.compile(interrupt_after=list(interrupt_after) if interrupt_after else None)


def build_repair_subgraph(static_repair, vue_repair):
    """保留验证图的 repair 调度名，让静态修复和 Vue 工具图各自执行。"""
    builder = StateGraph(WorkflowState, output_schema=VerificationOutput)
    builder.add_node("static_repair", static_repair)
    builder.add_node("vue_repair", vue_repair)
    builder.add_conditional_edges(START, lambda state: "vue_repair" if state["code_gen_type"] == "VUE_PROJECT"
                                  else "static_repair")
    builder.add_edge("static_repair", END)
    builder.add_edge("vue_repair", END)
    return builder.compile()
