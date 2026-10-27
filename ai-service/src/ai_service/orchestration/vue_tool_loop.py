"""Vue 工具图节点；所有恢复所需的轮次数据均保存为可序列化私有状态。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any, TYPE_CHECKING, TypedDict

from ai_service.models.tool_contract import validate_vue_tool_call
from ai_service.orchestration.routing import _raise_for_incomplete_model_turn
from ai_service.orchestration.state import WorkflowState

if TYPE_CHECKING:
    from ai_service.orchestration.workflow import GenerationWorkflow

logger = logging.getLogger(__name__)


class VueLoopProgress(TypedDict):
    """单个生成/修复阶段的进度；ordinal 只在新阶段初始化，恢复不得重置。"""

    content_parts: list[str]
    ordinal: int
    retry_count: int
    batch_limit: int
    file_plan: list[str]
    written_files: list[str]
    planning_turns: int
    image_retries: int
    image_used: bool
    turn: dict[str, Any]
    pending_tools: list[dict[str, Any]]
    tool_cursor: int
    next_step: str
    call_id_prefix: str
    started_at: str


class VueLoopState(WorkflowState, total=False):
    """私有字段仅存在于 Vue 子图命名空间，不回传父图。"""

    vue_loop: VueLoopProgress


def initial_progress(context: dict[str, Any], prefix: str, started_at: str) -> VueLoopProgress:
    return {
        "content_parts": [], "ordinal": 0, "retry_count": 0, "batch_limit": 2,
        "file_plan": [], "written_files": [], "planning_turns": 0,
        "image_retries": 0,
        "image_used": bool(context.get("imageAssetsApplied")) or not bool(context.get("imageAssets")),
        "turn": {}, "pending_tools": [], "tool_cursor": 0, "next_step": "model_turn",
        "call_id_prefix": prefix, "started_at": started_at,
    }


def create_vue_loop_nodes(
    workflow: GenerationWorkflow, emitter, thread_id: str, node: str,
    *, invoke_model=None,
):
    """生成和修复仅在模型入口不同，批次规则和工具执行路径完全共用。"""
    async def model_turn(state: VueLoopState):
        workflow._raise_if_cancelled(thread_id)
        context, loop = state["context"], state["vue_loop"]
        model_context = {
            **context, "toolResults": list(context["toolResults"]),
            "vueGeneration": {
                "maxToolCallsPerTurn": loop["batch_limit"],
                "remainingToolCalls": workflow.settings.vue_max_tool_calls - state.get("tool_call_count", 0),
                "filePlan": list(loop["file_plan"]), "writtenFiles": list(loop["written_files"]),
                "pendingFiles": [path for path in loop["file_plan"] if path not in loop["written_files"]],
                "retryCount": loop["retry_count"],
            },
        }
        await emitter.emit("node_status", node, data={
            "status": "progress", "message": f"正在生成项目文件，已写入 {len(loop['written_files'])} 个文件。",
        })
        started = asyncio.get_running_loop().time()
        if invoke_model is not None:
            turn = await invoke_model(model_context)
        elif node == "repair":
            turn = await workflow.model.repair(state.get("artifact", ""), model_context)
        else:
            turn = await workflow.model.generate("VUE_PROJECT", model_context)
        workflow._raise_if_cancelled(thread_id)
        logger.info(
            "Vue model turn request_id=%s node=%s finish_reason=%s output_tokens=%s elapsed_ms=%d tools=%d",
            state["request_id"], node, turn.finish_reason, turn.token_usage.get("output_tokens"),
            int((asyncio.get_running_loop().time() - started) * 1000), len(turn.tool_calls),
        )
        # 完整响应与待执行工具使用普通字典，避免 checkpoint 依赖模型类反序列化。
        return {"vue_loop": {**loop, "turn": asdict(turn)}}

    async def validate_turn(state: VueLoopState):
        workflow._raise_if_cancelled(thread_id)
        loop = {**state["vue_loop"]}
        context = {**state["context"]}
        turn = loop["turn"]
        calls = turn["tool_calls"]
        truncated = (turn["finish_reason"] or "").upper() in {"LENGTH", "MAX_TOKENS"}
        oversized = len(calls) > loop["batch_limit"]
        if truncated or oversized:
            if loop["retry_count"] >= 2:
                if truncated:
                    _raise_for_incomplete_model_turn(turn["finish_reason"])
                raise ValueError("VUE_TOOL_BATCH_LIMIT_EXCEEDED: Vue tool batch is too large")
            loop.update(retry_count=loop["retry_count"] + 1, batch_limit=1, next_step="model_turn", turn={})
            context["batchRetryFeedback"] = "上一轮响应未执行。请每轮只返回一个完整文件工具调用；过大的文件先拆分组件并更新 filePlan。"
            return {"vue_loop": loop, "context": context}
        _raise_for_incomplete_model_turn(turn["finish_reason"])
        usage = dict(state.get("token_usage", {}))
        for key, value in turn["token_usage"].items():
            usage[key] = usage.get(key, 0) + value
        update = {"context": context, "finish_reason": turn["finish_reason"], "token_usage": usage}
        if turn["content"]:
            loop["content_parts"] = [*loop["content_parts"], turn["content"]]
            await emitter.emit("content_delta", node, data={"content": turn["content"]})
        planning = False
        if turn["file_plan"]:
            changed_plan = turn["file_plan"] != loop["file_plan"]
            loop["file_plan"] = list(turn["file_plan"])
            if not calls and changed_plan:
                loop["planning_turns"] += 1
                if loop["planning_turns"] > 3:
                    raise ValueError("VUE_FILE_PLAN_LIMIT_EXCEEDED: too many planning-only turns")
                planning = True
        if planning:
            loop["next_step"] = "model_turn"
        elif not calls:
            if not loop["image_used"]:
                if loop["image_retries"] >= 2:
                    raise ValueError("VUE_IMAGE_USAGE_MISSING: searched images were not written into the page")
                loop["image_retries"] += 1
                context["imageUsageFeedback"] = "尚未将 imageAssets 中的真实图片 URL 写入页面。请在合适的首屏/卡片中使用图片，完成后再结束。"
                loop["next_step"] = "model_turn"
            else:
                if any(path not in loop["written_files"] for path in loop["file_plan"]):
                    raise ValueError("VUE_FILE_PLAN_INCOMPLETE: planned Vue files were not written")
                loop["next_step"] = "finish"
        else:
            if state.get("tool_call_count", 0) + len(calls) > workflow.settings.vue_max_tool_calls:
                raise ValueError("VUE_TOOL_CALL_LIMIT_EXCEEDED: Vue file tool budget exhausted")
            # 必须先整批校验，第二个工具无效时也不允许执行第一个。
            loop["pending_tools"] = [
                {"name": call["name"], "arguments": validate_vue_tool_call(call["name"], call["arguments"])}
                for call in calls
            ]
            loop.update(tool_cursor=0, next_step="execute_tool")
        # 工具已转成校验后的列表，不重复保存同一批大体积文件参数。
        loop["turn"] = {}
        return {**update, "vue_loop": loop}

    async def execute_tool(state: VueLoopState):
        workflow._raise_if_cancelled(thread_id)
        loop = {**state["vue_loop"]}
        call = loop["pending_tools"][loop["tool_cursor"]]
        arguments = call["arguments"]
        ordinal = loop["ordinal"] + 1
        tool_call_id = f"{loop['call_id_prefix']}:{ordinal}"
        result = await workflow._invoke_tool(
            emitter, node, call["name"], {**arguments, "codeGenType": state["code_gen_type"]},
            state["app_id"], state["request_id"], tool_call_id,
        )
        context = {**state["context"], "toolResults": [
            *state["context"]["toolResults"],
            {"toolCallId": tool_call_id, "tool": call["name"], "result": result},
        ]}
        written_files = set(loop["written_files"])
        if call["name"] in {"file_write", "file_modify"} and result.get("ok", True):
            written_files.add(arguments["relativeFilePath"])
            # 只认可成功写入源码的 URL，模型文字和搜索结果不能代表已配图。
            if any(asset["url"] in arguments.get("content", arguments.get("newContent", ""))
                   for asset in context.get("imageAssets", [])):
                loop["image_used"] = True
                context["imageAssetsApplied"] = True
        loop.update(ordinal=ordinal, tool_cursor=loop["tool_cursor"] + 1, written_files=sorted(written_files))
        return {"vue_loop": loop, "context": context, "tool_call_count": state.get("tool_call_count", 0) + 1}

    async def continue_loop(state: VueLoopState):
        workflow._raise_if_cancelled(thread_id)
        loop = {**state["vue_loop"]}
        if loop["tool_cursor"] < len(loop["pending_tools"]):
            loop["next_step"] = "execute_tool"
        else:
            loop.update(pending_tools=[], tool_cursor=0, next_step="model_turn")
        return {"vue_loop": loop}

    return {"model_turn": model_turn, "validate_turn": validate_turn,
            "execute_tool": execute_tool, "continue_loop": continue_loop}


async def run_vue_tool_loop(workflow: GenerationWorkflow, *, state, emitter, thread_id,
                            node, call_id_prefix, invoke_model):
    """兼容旧内部入口；主链路直接注册编译图以继承父图检查点。"""
    from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph, vue_recursion_limit

    graph = build_vue_tool_subgraph(
        workflow, emitter, thread_id, {}, node=node, invoke_model=invoke_model,
        call_id_prefix=call_id_prefix, legacy=True,
    )
    result = await graph.ainvoke(state, config={"recursion_limit": vue_recursion_limit(workflow.settings)})
    return {"content": result.pop("artifact"), **result}
