from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.state import WorkflowState

if TYPE_CHECKING:
    from ai_service.orchestration.workflow import GenerationWorkflow


def create_generation_nodes(
    workflow: GenerationWorkflow,
    emitter: EventEmitter,
    thread_id: str,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    """绑定单次请求依赖，节点闭包不进入图状态或检查点。"""
    async def generate_branch(state: WorkflowState) -> dict[str, Any]:
        """生成普通分支候选，并把结束原因传递给后续硬校验节点。"""
        branch = state["code_gen_type"]
        turn = await workflow.model.generate(branch, state["context"])
        if turn.content:
            await emitter.emit("content_delta", f"generate_{branch.lower()}", data={"content": turn.content})
        return {"artifact": turn.content, "finish_reason": turn.finish_reason, "token_usage": turn.token_usage}

    async def vue_agent(state: WorkflowState) -> dict[str, Any]:
        result = await workflow._run_vue_tool_loop(
            state=state,
            emitter=emitter,
            thread_id=thread_id,
            node="vue_agent",
            call_id_prefix=f"{state['request_id']}:vue-generate",
            invoke_model=lambda context: workflow.model.generate("VUE_PROJECT", context),
        )
        return {"artifact": result.pop("content"), **result}

    return {
        "generate_branch": generate_branch,
        "vue_agent": vue_agent,
    }
