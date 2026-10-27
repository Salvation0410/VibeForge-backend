from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TYPE_CHECKING

from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.state import WorkflowState

if TYPE_CHECKING:
    from ai_service.orchestration.workflow import GenerationWorkflow


async def start_node(workflow, emitter, thread_id, terminal, name, state):
    """阶段入口与多节点子图共用相同的取消及公共事件边界。"""
    terminal.update(repair_count=state.get("repair_count", 0), tool_call_count=state.get("tool_call_count", 0))
    workflow._raise_if_cancelled(thread_id)
    await emitter.node_status(name, "started")


async def complete_node(workflow, emitter, thread_id, terminal, name, state, started_at):
    """只保存脱敏业务摘要，内部调度节点不另发公共节点事件。"""
    terminal.update(repair_count=state.get("repair_count", 0), tool_call_count=state.get("tool_call_count", 0))
    await workflow.checkpoint.save(thread_id, workflow._checkpoint_payload(state, name))
    await emitter.node_status(name, "completed")
    if workflow.tracer is not None:
        await workflow.tracer.record(
            name=name,
            metadata={
                "request_id": state["request_id"], "app_id": state["app_id"],
                "code_gen_type": state["code_gen_type"],
                "repair_count": state.get("repair_count", 0),
                "tool_call_count": state.get("tool_call_count", 0),
            },
            status="completed", started_at=started_at,
        )


def guarded(workflow: GenerationWorkflow, emitter: EventEmitter, thread_id: str,
            terminal: dict[str, Any], name: str, function):
    """统一节点取消检查、状态事件和脱敏状态摘要。"""
    async def node(state: WorkflowState) -> dict[str, Any]:
        started_at = datetime.now(UTC)
        await start_node(workflow, emitter, thread_id, terminal, name, state)
        update = await function(state)
        merged = {**state, **update}
        await complete_node(workflow, emitter, thread_id, terminal, name, merged, started_at)
        return update

    return node
