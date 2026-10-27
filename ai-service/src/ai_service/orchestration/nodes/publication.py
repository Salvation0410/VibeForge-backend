from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.state import WorkflowState

if TYPE_CHECKING:
    from ai_service.orchestration.workflow import GenerationWorkflow


def create_publication_nodes(
    workflow: GenerationWorkflow,
    emitter: EventEmitter,
    thread_id: str,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    """绑定单次请求依赖，节点闭包不进入图状态或检查点。"""
    def completion_data(state: WorkflowState) -> dict[str, Any]:
        """构造不含源码的成功摘要，避免 completed 重复传输完整产物。"""
        data: dict[str, Any] = {
            "threadId": state["thread_id"],
            "codeGenType": state["code_gen_type"],
            "qualityPassed": state.get("quality_passed", False),
            "repairCount": state.get("repair_count", 0),
            "toolCallCount": state.get("tool_call_count", 0),
        }
        if state["code_gen_type"] in {"HTML", "MULTI_FILE"}:
            publication = state.get("publish", {})
            data.update(
                published=publication.get("published") is True,
                versionId=publication.get("versionId"),
                artifactHashes=publication.get("hashes", {}),
            )
        else:
            data["built"] = state.get("build", {}).get("built") is True
        return data

    async def artifact_publish(state: WorkflowState) -> dict[str, Any]:
        """请求 Spring 重新校验并发布 HTML 或多文件候选，拒绝时不得进入完成终态。"""
        call_id = f"{state['request_id']}:artifact_publish"

        def remember_publication(result: dict[str, Any]) -> None:
            """在附属事件发送前记录 Spring 权威发布结果，防止成功终态被反转。"""
            if not result.get("published", False):
                raise ValueError("Spring rejected artifact publication")
            terminal["published"] = True
            terminal["data"] = {
                "threadId": state["thread_id"],
                "codeGenType": state["code_gen_type"],
                "qualityPassed": state.get("quality_passed", False),
                "repairCount": state.get("repair_count", 0),
                "toolCallCount": state.get("tool_call_count", 0),
                "published": True,
                "versionId": result.get("versionId"),
                "artifactHashes": result.get("hashes", {}),
            }

        result = await workflow._invoke_tool(
            emitter,
            "artifact_publish",
            "artifact_publish",
            {
                "codeGenType": state["code_gen_type"],
                "artifact": state.get("artifact", ""),
                "engine": "langgraph",
                "finishReason": state.get("finish_reason") or "",
            },
            state["app_id"],
            state["request_id"],
            call_id,
            on_result=remember_publication,
        )
        return {"publish": result}

    async def artifact_publish_node(state: WorkflowState) -> dict[str, Any]:
        """在发布前完成取消检查和 checkpoint，发布后只发送不可失败的内存事件。"""
        workflow._raise_if_cancelled(thread_id)
        await emitter.node_status("artifact_publish", "started")
        await workflow.checkpoint.save(thread_id, workflow._checkpoint_payload(state, "artifact_publish_pending"))
        update = await artifact_publish(state)
        await emitter.node_status("artifact_publish", "completed")
        return update

    async def finalize(state: WorkflowState) -> dict[str, Any]:
        """发送成功终态；版本化产物已提交后不再让外部 checkpoint 反转业务结果。"""
        workflow._raise_if_cancelled(thread_id)
        await emitter.node_status("finalize", "started")
        if state["code_gen_type"] not in {"HTML", "MULTI_FILE"}:
            await workflow.checkpoint.save(thread_id, workflow._checkpoint_payload(state, "finalize"))
        await emitter.node_status("finalize", "completed")
        await emitter.emit(
            "completed",
            "finalize",
            data=completion_data(state),
        )
        terminal["completed"] = True
        return {}

    return {
        "artifact_publish": artifact_publish,
        "artifact_publish_node": artifact_publish_node,
        "finalize": finalize,
    }
