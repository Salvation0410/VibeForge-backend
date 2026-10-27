from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, TYPE_CHECKING

from ai_service.models.base import ModelTurn
from ai_service.models.tool_contract import validate_vue_tool_call
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.routing import _raise_for_incomplete_model_turn
from ai_service.orchestration.state import WorkflowState

if TYPE_CHECKING:
    from ai_service.orchestration.workflow import GenerationWorkflow

logger = logging.getLogger(__name__)


async def run_vue_tool_loop(
    workflow: GenerationWorkflow,
    *,
    state: WorkflowState,
    emitter: EventEmitter,
    thread_id: str,
    node: str,
    call_id_prefix: str,
    invoke_model: Callable[[dict[str, Any]], Awaitable[ModelTurn]],
) -> dict[str, Any]:
    """运行生成与修复共享的 Vue 工具循环，并维护跨阶段总预算。"""
    context = {
        **state["context"],
        "toolResults": list(state["context"].get("toolResults", [])),
    }
    content_parts: list[str] = []
    tool_count = state.get("tool_call_count", 0)
    ordinal = 0
    finish_reason: str | None = None
    token_usage: dict[str, int] = {}
    retry_count = 0
    batch_limit = 2
    file_plan: list[str] = []
    written_files: set[str] = set()
    planning_turns = 0
    image_retries = 0
    image_used = bool(context.get("imageAssetsApplied")) or not bool(context.get("imageAssets"))

    while True:
        workflow._raise_if_cancelled(thread_id)
        model_context = {
            **context,
            "toolResults": list(context["toolResults"]),
            "vueGeneration": {
                "maxToolCallsPerTurn": batch_limit,
                "remainingToolCalls": workflow.settings.vue_max_tool_calls - tool_count,
                "filePlan": file_plan,
                "writtenFiles": sorted(written_files),
                "pendingFiles": [path for path in file_plan if path not in written_files],
                "retryCount": retry_count,
            },
        }
        await emitter.emit("node_status", node, data={
            "status": "progress", "message": f"正在生成项目文件，已写入 {len(written_files)} 个文件。",
        })
        started = asyncio.get_running_loop().time()
        turn = await invoke_model(model_context)
        logger.info(
            "Vue model turn request_id=%s node=%s finish_reason=%s output_tokens=%s elapsed_ms=%d tools=%d",
            state["request_id"], node, turn.finish_reason, turn.token_usage.get("output_tokens"),
            int((asyncio.get_running_loop().time() - started) * 1000), len(turn.tool_calls),
        )
        # 截断响应与超批次响应整轮丢弃；重试不占文件工具预算，也不执行部分请求。
        truncated = (turn.finish_reason or "").upper() in {"LENGTH", "MAX_TOKENS"}
        oversized = len(turn.tool_calls) > batch_limit
        if truncated or oversized:
            if retry_count >= 2:
                if truncated:
                    _raise_for_incomplete_model_turn(turn.finish_reason)
                raise ValueError("VUE_TOOL_BATCH_LIMIT_EXCEEDED: Vue tool batch is too large")
            retry_count += 1
            batch_limit = 1
            context["batchRetryFeedback"] = "上一轮响应未执行。请每轮只返回一个完整文件工具调用；过大的文件先拆分组件并更新 filePlan。"
            continue
        _raise_for_incomplete_model_turn(turn.finish_reason)
        finish_reason = turn.finish_reason
        for key, value in turn.token_usage.items():
            token_usage[key] = token_usage.get(key, 0) + value
        if turn.content:
            content_parts.append(turn.content)
            await emitter.emit("content_delta", node, data={"content": turn.content})
        if turn.file_plan:
            changed_plan = turn.file_plan != file_plan
            file_plan = turn.file_plan
            if not turn.tool_calls and changed_plan:
                planning_turns += 1
                if planning_turns > 3:
                    raise ValueError("VUE_FILE_PLAN_LIMIT_EXCEEDED: too many planning-only turns")
                continue
        if not turn.tool_calls:
            if not image_used:
                if image_retries >= 2:
                    raise ValueError("VUE_IMAGE_USAGE_MISSING: searched images were not written into the page")
                image_retries += 1
                context["imageUsageFeedback"] = "尚未将 imageAssets 中的真实图片 URL 写入页面。请在合适的首屏/卡片中使用图片，完成后再结束。"
                continue
            if any(path not in written_files for path in file_plan):
                raise ValueError("VUE_FILE_PLAN_INCOMPLETE: planned Vue files were not written")
            break

        # 不得静默丢弃本轮未执行的写入请求，否则半成品会进入校验与构建。
        if tool_count + len(turn.tool_calls) > workflow.settings.vue_max_tool_calls:
            raise ValueError("VUE_TOOL_CALL_LIMIT_EXCEEDED: Vue file tool budget exhausted")

        validated_arguments = [validate_vue_tool_call(call.name, call.arguments) for call in turn.tool_calls]
        for call, arguments in zip(turn.tool_calls, validated_arguments):
            workflow._raise_if_cancelled(thread_id)
            tool_count += 1
            ordinal += 1
            tool_call_id = f"{call_id_prefix}:{ordinal}"
            result = await workflow._invoke_tool(
                emitter,
                node,
                call.name,
                {**arguments, "codeGenType": state["code_gen_type"]},
                state["app_id"],
                state["request_id"],
                tool_call_id,
            )
            context["toolResults"].append(
                {"toolCallId": tool_call_id, "tool": call.name, "result": result}
            )
            if call.name in {"file_write", "file_modify"} and result.get("ok", True):
                written_files.add(arguments["relativeFilePath"])
                # 只认可成功写入源码的 URL；口头说明、搜索成功均不能代表页面已配图。
                if any(asset["url"] in arguments.get("content", arguments.get("newContent", "")) for asset in context.get("imageAssets", [])):
                    image_used = True
                    context["imageAssetsApplied"] = True

    return {
        "content": "\n".join(content_parts),
        "context": context,
        "tool_call_count": tool_count,
        "finish_reason": finish_reason,
        "token_usage": token_usage,
    }
