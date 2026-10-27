"""静态模型调用边界：展示增量与最终候选分离，兼容不支持流式的模型。"""

from ai_service.api.schemas import StaticContentData
from ai_service.orchestration.routing import _raise_for_incomplete_model_turn


async def invoke_static_model(workflow, state, emitter, thread_id, *, repair_context=None):
    repair = repair_context is not None
    node = "repair" if repair else f"generate_{state['code_gen_type'].lower()}"
    metadata = {"repairCount": state.get("repair_count", 0) + 1} if repair else {}
    method = getattr(workflow.model, "repair_stream" if repair else "generate_stream", None)
    workflow._raise_if_cancelled(thread_id)

    async def display_delta(text):
        workflow._raise_if_cancelled(thread_id)
        if text:
            data = StaticContentData(content=text, content_mode="display_delta").model_dump(by_alias=True)
            await emitter.emit("content_delta", node, data={**data, **metadata})

    if callable(method):
        turn = await method(state.get("artifact", "") if repair else state["code_gen_type"],
                            repair_context if repair else state["context"], on_delta=display_delta)
        workflow._raise_if_cancelled(thread_id)
        # 增量只能展示，结束原因通过检查后才能把完整候选送入硬校验与 Spring 缓存。
        _raise_for_incomplete_model_turn(turn.finish_reason)
        if (turn.finish_reason or "").upper() not in {"STOP", "END_TURN"}:
            raise ValueError("MODEL_STREAM_INCOMPLETE: model stream has no normal finish marker")
        data = StaticContentData(content=turn.content, content_mode="candidate").model_dump(by_alias=True)
        await emitter.emit("content_delta", node, data={**data, **metadata})
    else:
        turn = await (workflow.model.repair(state.get("artifact", ""), repair_context) if repair
                      else workflow.model.generate(state["code_gen_type"], state["context"]))
        workflow._raise_if_cancelled(thread_id)
        if turn.content or repair:
            await emitter.emit("content_delta", node, data={"content": turn.content, **metadata})
    return turn
