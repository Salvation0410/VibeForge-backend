from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.state import WorkflowState
from ai_service.orchestration.routing import _raise_for_incomplete_model_turn
from ai_service.orchestration.multi_agent_review import run_multi_agent_review

if TYPE_CHECKING:
    from ai_service.orchestration.workflow import GenerationWorkflow


def _vue_snapshot_artifact(snapshot: dict[str, Any]) -> str:
    """将 Vue 源码快照序列化为键顺序稳定、无多余空白的确定性 JSON。"""
    return json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _vue_snapshot_event_result(snapshot: dict[str, Any]) -> dict[str, Any]:
    """仅暴露 Vue 快照统计元数据，避免源码进入工作流事件。"""
    return {
        "eligibleFileCount": snapshot["eligibleFileCount"],
        "includedFileCount": snapshot["includedFileCount"],
        "omittedFileCount": snapshot["omittedFileCount"],
        "truncated": snapshot["truncated"],
    }


def create_verification_nodes(
    workflow: GenerationWorkflow,
    emitter: EventEmitter,
    thread_id: str,
    terminal: dict[str, Any],
) -> dict[str, Any]:
    """绑定单次请求依赖，节点闭包不进入图状态或检查点。"""
    async def artifact_validation(state: WorkflowState) -> dict[str, Any]:
        """先拒绝截断或被过滤的响应，再调用 Spring 执行产物硬校验。"""
        _raise_for_incomplete_model_turn(state.get("finish_reason"))
        call_id = f"{state['request_id']}:artifact_validation:{state.get('repair_count', 0)}"
        result = await workflow._invoke_tool(
            emitter,
            "artifact_validation",
            "artifact_validate",
            {"codeGenType": state["code_gen_type"], "artifact": state.get("artifact", "")},
            state["app_id"],
            state["request_id"],
            call_id,
        )
        return {"validation": result}

    async def project_build(state: WorkflowState) -> dict[str, Any]:
        call_id = f"{state['request_id']}:project_build:{state.get('repair_count', 0)}"
        result = await workflow._invoke_tool(
            emitter,
            "project_build",
            "project_build",
            {"codeGenType": state["code_gen_type"]},
            state["app_id"],
            state["request_id"],
            call_id,
        )
        return {"build": result}

    async def quality_review(state: WorkflowState) -> dict[str, Any]:
        """仅对已通过确定性硬校验的候选执行模型质量检查。"""
        if not state.get("validation", {}).get("valid", False):
            return {"quality_passed": False}
        review_artifact = state.get("artifact", "")
        repair_count = state.get("repair_count", 0)
        multi_agent_enabled = (
            state["code_gen_type"] == "VUE_PROJECT"
            and workflow.settings.multi_agent_review_enabled
        )
        uses_vue_snapshot = (
            state["code_gen_type"] == "VUE_PROJECT"
            and state.get("build", {}).get("built") is True
            and (multi_agent_enabled or repair_count > 0)
        )
        if uses_vue_snapshot:
            try:
                snapshot = await workflow._invoke_tool(
                    emitter,
                    "quality_review",
                    "vue_source_snapshot",
                    {"codeGenType": "VUE_PROJECT"},
                    state["app_id"],
                    state["request_id"],
                    f"{state['request_id']}:vue-source-snapshot:{repair_count}",
                    event_result=_vue_snapshot_event_result,
                )
            except Exception:
                if multi_agent_enabled:
                    raise RuntimeError(
                        "MULTI_AGENT_REVIEW_SNAPSHOT_ERROR: "
                        "Vue source snapshot is unavailable"
                    ) from None
                raise
            review_artifact = _vue_snapshot_artifact(snapshot)
        if multi_agent_enabled:
            review_context = {
                "prompt": state["prompt"],
                "codeGenType": "VUE_PROJECT",
                "validation": state.get("validation"),
                "build": state.get("build"),
            }
            workflow._raise_if_cancelled(thread_id)
            result = await run_multi_agent_review(
                workflow.model,
                review_artifact,
                review_context,
                timeout_seconds=workflow.settings.multi_agent_review_timeout_seconds,
            )
            workflow._raise_if_cancelled(thread_id)
            return {
                "quality_passed": result.passed,
                "repair_feedback": result.repair_feedback,
            }
        review_context = {
            **state["context"],
            "validation": state.get("validation"),
            "build": state.get("build"),
        }
        if uses_vue_snapshot:
            try:
                passed = await workflow.model.review(review_artifact, review_context)
            except Exception as exc:
                raise RuntimeError("Vue source snapshot review failed") from exc
        else:
            passed = await workflow.model.review(review_artifact, review_context)
        return {"quality_passed": passed}

    async def repair(state: WorkflowState) -> dict[str, Any]:
        """生成完整修复候选并更新结束元数据，供下一轮重新校验。"""
        count = state.get("repair_count", 0) + 1
        repair_context = {
            **state["context"],
            "codeGenType": state["code_gen_type"],
            "repairCount": count,
            "validation": state.get("validation"),
            "build": state.get("build"),
        }
        if state.get("repair_feedback"):
            repair_context["qualityReview"] = {
                "repairFeedback": state["repair_feedback"]
            }
        if state["code_gen_type"] == "VUE_PROJECT":
            result = await workflow._run_vue_tool_loop(
                state={**state, "context": repair_context},
                emitter=emitter,
                thread_id=thread_id,
                node="repair",
                call_id_prefix=f"{state['request_id']}:vue-repair:{count}",
                invoke_model=lambda context: workflow.model.repair(state.get("artifact", ""), context),
            )
            next_context = {
                key: value
                for key, value in result["context"].items()
                if key != "qualityReview"
            }
            return {
                "context": next_context,
                "build": {},
                "tool_call_count": result["tool_call_count"],
                "repair_count": count,
                "finish_reason": result["finish_reason"],
                "token_usage": result["token_usage"],
                "repair_feedback": "",
            }
        turn = await workflow.model.repair(
            state.get("artifact", ""),
            repair_context,
        )
        await emitter.emit("content_delta", "repair", data={"content": turn.content, "repairCount": count})
        return {
            "artifact": turn.content,
            "build": {},
            "repair_count": count,
            "finish_reason": turn.finish_reason,
            "token_usage": turn.token_usage,
        }

    async def fail_quality(state: WorkflowState) -> dict[str, Any]:
        """在硬校验或质量检查耗尽修复次数后生成明确失败终态。"""
        build = state.get("build", {})
        if not build.get("built", True):
            code = build.get("errorCode") or "VUE_BUILD_FAILED"
            message = build.get("message") or "Vue project build failed"
            raise ValueError(f"{code}: {message}")
        errors = state.get("validation", {}).get("errors", [])
        raise ValueError(f"Artifact did not pass validation or quality review: {errors}")

    return {
        "artifact_validation": artifact_validation,
        "project_build": project_build,
        "quality_review": quality_review,
        "repair": repair,
        "fail_quality": fail_quality,
    }
