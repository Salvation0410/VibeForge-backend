"""Vue 工具图节点；所有恢复所需的轮次数据均保存为可序列化私有状态。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any, TYPE_CHECKING, TypedDict

from ai_service.models.tool_contract import validate_vue_tool_call
from ai_service.orchestration.routing import _raise_for_incomplete_model_turn
from ai_service.orchestration.repair_progress import vue_snapshot_fingerprint
from ai_service.orchestration.vue_read_stall import repair_reads_stalled, read_repetition_stats, phase_has_successful_write, record_visible_read
from ai_service.orchestration.vue_budget import (
    NEXT_REPAIR_TOOL_RESERVE, REPAIR_READ_BATCH_LIMIT, REPAIR_TOOL_RESERVE,
    budget_counts, is_complete_file_read,
)
from ai_service.orchestration.subgraphs.schemas import RepairState
from ai_service.orchestration.repair_diagnostics import save_repair_failure
from ai_service.models.input_review import InputReviewError
from ai_service.orchestration.vue_repair_workset import (
    repair_work_payload, prepare_work_turn, record_work_result, finish_work_batch, require_write_after_reads,
)

if TYPE_CHECKING:
    from ai_service.orchestration.workflow import GenerationWorkflow

logger = logging.getLogger(__name__)


class VueLoopProgress(TypedDict):
    """单个生成/修复阶段的进度；ordinal 只在新阶段初始化，恢复不得重置。"""

    content_parts: list[str]
    ordinal: int
    retry_count: int
    format_retries: int
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
    batch_has_successful_write: bool
    write_fingerprints: list[str]
    successful_write: bool
    read_only_batches: int
    prewrite_warning: bool
    prewrite_warning_ordinal: int
    prewrite_warning_visible_total: int
    visible_files: dict[str, str]
    visible_repeat_count: int
    visible_repeat_total: int
    omitted_reread_count: int
    repair_work: dict[str, Any]


class VueLoopState(RepairState, total=False):
    """私有字段仅存在于 Vue 子图命名空间，不回传父图。"""

    vue_loop: VueLoopProgress
    vue_tool_results: list[dict[str, Any]]


def initial_progress(context: dict[str, Any], prefix: str, started_at: str) -> VueLoopProgress:
    return {
        "content_parts": [], "ordinal": 0, "retry_count": 0, "format_retries": 0, "batch_limit": 2,
        "file_plan": [], "written_files": [], "planning_turns": 0,
        "image_retries": 0,
        "image_used": bool(context.get("imageAssetsApplied")) or not bool(context.get("imageAssets")),
        "turn": {}, "pending_tools": [], "tool_cursor": 0, "next_step": "model_turn",
        "call_id_prefix": prefix, "started_at": started_at,
        "batch_has_successful_write": False, "write_fingerprints": [],
        "successful_write": False, "read_only_batches": 0,
        "prewrite_warning": False, "prewrite_warning_ordinal": 0,
        "prewrite_warning_visible_total": 0,
        "visible_files": {}, "visible_repeat_count": 0, "visible_repeat_total": 0, "omitted_reread_count": 0,
        "repair_work": {},
    }


def create_vue_loop_nodes(
    workflow: GenerationWorkflow, emitter, thread_id: str, node: str,
    *, invoke_model=None, repair_read_guard=False, repair_write_guard=False, repair_budget_guard=False,
):
    """生成和修复仅在模型入口不同，批次规则和工具执行路径完全共用。"""
    def log_budget(state, loop, *, reason, requested_count=0):
        counts = budget_counts(state["vue_tool_results"], loop["call_id_prefix"])
        repeats = read_repetition_stats(state["vue_tool_results"], loop["call_id_prefix"])
        # 终态摘要可能停在上个已完成节点，失败现场必须使用子图真实累计数。
        log = logger.warning if reason in {"TOOL_BUDGET_EXHAUSTED", "PREWRITE_READ_STALLED"} else logger.info
        log("Vue tool budget request_id=%s node=%s reason=%s repair_count=%d tool_count=%d "
            "read_count=%d write_count=%d other_count=%d phase_tool_count=%d "
            "phase_read_count=%d phase_write_count=%d phase_other_count=%d "
            "remaining=%d requested_count=%d read_batches=%d repeated_read_count=%d visible_repeat_count=%d omitted_reread_count=%d",
            state["request_id"], node, reason, state.get("repair_count", 0), state.get("tool_call_count", 0),
            counts["read_count"], counts["write_count"], counts["other_count"], counts["phase_tool_count"],
            counts["phase_read_count"], counts["phase_write_count"], counts["phase_other_count"],
            max(0, workflow.settings.vue_max_tool_calls - state.get("tool_call_count", 0)),
            requested_count, loop.get("read_only_batches", 0), repeats["total"],
            loop.get("visible_repeat_total", 0), loop.get("omitted_reread_count", 0))

    async def model_turn(state: VueLoopState):
        workflow._raise_if_cancelled(thread_id)
        context, loop = state["context"], state["vue_loop"]
        model_context = {
            **context, "toolResults": list(state["vue_tool_results"]),
            "vueGeneration": {
                # 模型调用无隐式对话记忆，必须明确告知已完成规划，避免每轮重新执行“首次规划”。
                "phase": "write_files" if loop["file_plan"] else (
                    # 首次生成的基线仍为空，但修复面对的是本请求刚写入的项目，不能重新提示首次规划。
                    "modify_files" if node == "repair" or context.get("currentArtifact", {}).get("exists")
                    else "plan_files"
                ),
                "maxToolCallsPerTurn": loop["batch_limit"],
                "remainingToolCalls": workflow.settings.vue_max_tool_calls - state.get("tool_call_count", 0),
                "filePlan": list(loop["file_plan"]), "writtenFiles": list(loop["written_files"]),
                "pendingFiles": [path for path in loop["file_plan"] if path not in loop["written_files"]],
                "retryCount": loop["retry_count"],
            },
        }
        if loop.get("repair_work"):
            model_context["repairWork"] = repair_work_payload(loop["repair_work"])
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
        serialized = asdict(turn)
        visibility = serialized.pop("vue_visible_files", None)
        return {"vue_loop": {**loop, "turn": serialized,
                             "visible_files": dict(visibility) if isinstance(visibility, dict) else {}}}

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
            context["batchRetryFeedback"] = (
                "上一轮响应未执行。每轮只返回一个完整文件工具调用；按repairWork缩小当前repairPlan，禁止改用首次filePlan。"
                if loop.get("repair_work") else
                "上一轮响应未执行。请每轮只返回一个完整文件工具调用；过大的文件先拆分组件并更新 filePlan。"
            )
            return {"vue_loop": loop, **({"context": context} if context != state["context"] else {})}
        _raise_for_incomplete_model_turn(turn["finish_reason"])
        if turn.get("vue_output_invalid"):
            # 无效响应整轮丢弃；计数跨成功轮次保留，避免不消耗工具的格式重试无限循环。
            retries = loop.get("format_retries", 0)
            if retries >= 2:
                raise ValueError("VUE_MODEL_INVALID_OUTPUT: Vue 工具响应格式错误，请重新生成")
            loop.update(format_retries=retries + 1, next_step="model_turn", turn={})
            context["vueFormatFeedback"] = (
                "上一轮格式错误，未执行任何工具。只返回一个完整 JSON 对象，禁止在 JSON 前后添加说明或输出多段 JSON。"
                + ("按repairWork使用content、repairPlan、toolCalls或单独completedIssueIndex；禁止首次filePlan。"
                   if loop.get("repair_work") else
                   "使用 content、filePlan、toolCalls 字段；按 pendingFiles 继续写入，每轮遵守 maxToolCallsPerTurn。")
            )
            logger.warning("Vue invalid model output request_id=%s node=%s retry=%d",
                           state["request_id"], node, retries + 1)
            return {"vue_loop": loop, "context": context}
        work_action = None
        if loop.get("repair_work"):
            try:
                work, work_action = prepare_work_turn(loop["repair_work"], turn)
                require_write_after_reads(work, calls)
            except ValueError:
                retries = loop.get("format_retries", 0)
                if retries >= 2:
                    raise
                loop.update(format_retries=retries + 1, next_step="model_turn", turn={})
                context["repairPlanFeedback"] = (
                    "上一轮修复计划未执行。按repairWork中第一个未完成问题返回repairPlan(issueIndex,files)，"
                    "files仅从allowedFiles选择最多3个；整批工具只能操作活动文件，禁止目录扫描。"
                    "已有计划直接继续必要读取及写入；连续三个完整纯读批次后必须先写入。"
                    "成功写入后用completedIssueIndex单独声明当前项待验证，不能跳项或仅声称全部完成。"
                )
                return {"vue_loop": loop, "context": context}
            loop["repair_work"] = work
        usage = dict(state.get("token_usage", {}))
        for key, value in turn["token_usage"].items():
            usage[key] = usage.get(key, 0) + value
        update = {"finish_reason": turn["finish_reason"], "token_usage": usage}
        if turn["content"]:
            loop["content_parts"] = [*loop["content_parts"], turn["content"]]
            await emitter.emit("content_delta", node, data={"content": turn["content"]})
        planning = False
        if turn["file_plan"]:
            loop["file_plan"] = list(turn["file_plan"])
            if not calls and any(path not in loop["written_files"] for path in loop["file_plan"]):
                loop["planning_turns"] += 1
                if loop["planning_turns"] > 3:
                    raise ValueError("VUE_FILE_PLAN_LIMIT_EXCEEDED: too many planning-only turns")
                planning = True
                context["planningFeedback"] = "文件规划已收到。下一轮必须调用 file_write 写入 pendingFiles 中尚未完成的文件，不要再次仅返回文件规划；全部写入后才可结束。"
        if work_action == "continue":
            loop["next_step"] = "model_turn"
        elif planning:
            loop["next_step"] = "model_turn"
        elif not calls:
            if repair_read_guard and loop.get("prewrite_warning", False) and not loop.get("successful_write", False):
                log_budget(state, loop, reason="PREWRITE_READ_STALLED")
                raise ValueError("VUE_REPAIR_READ_STALLED: 重复读取纠偏后未成功写入，不能宣告修复完成")
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
                log_budget(state, loop, reason="TOOL_BUDGET_EXHAUSTED", requested_count=len(calls))
                raise ValueError("VUE_TOOL_CALL_LIMIT_EXCEEDED: Vue file tool budget exhausted")
            # 必须先整批校验，第二个工具无效时也不允许执行第一个。
            loop["pending_tools"] = [
                {"name": call["name"], "arguments": validate_vue_tool_call(call["name"], call["arguments"])}
                for call in calls
            ]
            loop.update(tool_cursor=0, next_step="execute_tool", batch_has_successful_write=False)
        # 工具已转成校验后的列表，不重复保存同一批大体积文件参数。
        loop["turn"] = {}
        if context != state["context"]:
            update["context"] = context
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
        loop = record_visible_read(loop, {"tool": call["name"], "arguments": arguments, "result": result},
                                   state["vue_tool_results"])
        if loop.get("repair_work"):
            loop["repair_work"] = record_work_result(loop["repair_work"], {"tool": call["name"], "result": result})
        tool_results = [
            *state["vue_tool_results"],
            {"toolCallId": tool_call_id, "tool": call["name"], "result": result,
             # 路径用于模型关联读取与后续修改，禁止将写入源码复制进历史参数。
             "arguments": {key: value for key, value in arguments.items()
                           if key in {"relativeFilePath", "relativeDirPath", "query"}}},
        ]
        context = state["context"]
        update = {}
        written_files = set(loop["written_files"])
        if call["name"] in {"file_write", "file_modify"} and result.get("ok") is True:
            # 只有明确成功的写入才值得在整批结束后取证，未知结果不作为触发依据。
            loop["batch_has_successful_write"] = True
            loop["successful_write"] = True
        if call["name"] in {"file_write", "file_modify"} and result.get("ok", True):
            written_files.add(arguments["relativeFilePath"])
            # 只认可成功写入源码的 URL，模型文字和搜索结果不能代表已配图。
            if any(asset["url"] in arguments.get("content", arguments.get("newContent", ""))
                   for asset in context.get("imageAssets", [])):
                loop["image_used"] = True
                if not context.get("imageAssetsApplied"):
                    update["context"] = {**context, "imageAssetsApplied": True}
        loop.update(ordinal=ordinal, tool_cursor=loop["tool_cursor"] + 1, written_files=sorted(written_files))
        return {**update, "vue_loop": loop, "vue_tool_results": tool_results,
                "tool_call_count": state.get("tool_call_count", 0) + 1}

    async def continue_loop(state: VueLoopState):
        workflow._raise_if_cancelled(thread_id)
        loop = {**state["vue_loop"]}
        if loop["tool_cursor"] < len(loop["pending_tools"]):
            loop["next_step"] = "execute_tool"
        else:
            batch = loop["pending_tools"]
            if loop.get("repair_work"):
                loop["repair_work"] = finish_work_batch(loop["repair_work"],
                                                       state["vue_tool_results"][-len(batch):] if batch else [])
            # 只在完整纯读批次结束后纠偏，不丢弃已请求工具，也不将旧候选直接交回验证。
            prewrite = repair_read_guard and batch and all(call["name"] == "file_read" for call in batch)
            if prewrite and not loop.get("successful_write", False) and not phase_has_successful_write(
                state["vue_tool_results"], loop["call_id_prefix"],
            ):
                repeated = loop.get("visible_repeat_count", 0)
                if repeated >= 4:
                    if not loop.get("prewrite_warning", False):
                        loop.update(prewrite_warning=True, prewrite_warning_ordinal=loop["ordinal"],
                                    prewrite_warning_visible_total=loop.get("visible_repeat_total", 0))
                        log_budget(state, loop, reason="PREWRITE_READ_WARNING")
                        context = {**state["context"], "repairReadFeedback": (
                            "本阶段已连续重复读取相同完整源码。请依据当前可见的最新结果修复具体问题，"
                            "核对事件发出方、中间转发、父级监听和状态更新的整条调用链；"
                            "不要为确认重复读取。确实缺少必要依赖时才读取新文件。修复后交回验证。"
                        )}
                        loop.update(pending_tools=[], tool_cursor=0, next_step="model_turn")
                        return {"vue_loop": loop, "context": context}
                    if loop.get("visible_repeat_total", 0) - loop.get("prewrite_warning_visible_total", 0) >= 4:
                        log_budget(state, loop, reason="PREWRITE_READ_STALLED")
                        raise ValueError("VUE_REPAIR_READ_STALLED: 修复持续重复读取且尚未成功写入，本轮已停止")
            if repair_budget_guard:
                batch = loop["pending_tools"]
                results = state["vue_tool_results"][-len(batch):] if batch else []
                # 新路径也计入读取开销；混合批次或不完整读取重新累计，不能跨写入累加。
                pure_reads = bool(batch) and all(call["name"] == "file_read" for call in batch)
                complete_reads = pure_reads and len(results) == len(batch) and all(
                    is_complete_file_read(item) for item in results
                )
                loop["read_only_batches"] = (
                    loop.get("read_only_batches", 0) + 1
                    if complete_reads and loop.get("successful_write", False) else 0
                )
            write_cycle = False
            if repair_write_guard and loop.get("batch_has_successful_write", False):
                # 不进入模型历史；事件仅公开计数，失败沿原工具错误路径传播。
                snapshot = await workflow._invoke_tool(
                    emitter, node, "vue_source_snapshot", {"codeGenType": state["code_gen_type"]},
                    state["app_id"], state["request_id"],
                    f"{loop['call_id_prefix']}:source-snapshot:{loop['ordinal']}",
                    event_result=lambda result: {key: result.get(key) for key in (
                        "eligibleFileCount", "includedFileCount", "omittedFileCount", "truncated",
                    )},
                )
                workflow._raise_if_cancelled(thread_id)
                fingerprint = vue_snapshot_fingerprint(snapshot)
                previous = loop.get("write_fingerprints", [])
                if fingerprint is not None:
                    write_cycle = fingerprint in previous
                    if not write_cycle:
                        loop["write_fingerprints"] = [*previous, fingerprint]
            ready = loop["image_used"] and all(
                path in loop["written_files"] for path in loop["file_plan"]
            )
            # 完整批次执行完才判断，不能跳过同批次后续写入；返回后仍须强制构建与审查。
            read_stall = (
                repair_read_guard and bool(loop["pending_tools"])
                and all(call["name"] == "file_read" for call in loop["pending_tools"])
                and repair_reads_stalled(state["vue_tool_results"], loop["call_id_prefix"])
            )
            budget_reason = None
            if repair_budget_guard and ready and loop.get("successful_write", False):
                # 当前轮计数已由修复入口递增；缺失旧字段时不推断仍有下一轮修复机会。
                has_next_repair = (
                    state.get("repair_count", workflow.settings.max_repair_attempts)
                    < workflow.settings.max_repair_attempts
                )
                reserve = NEXT_REPAIR_TOOL_RESERVE if has_next_repair else REPAIR_TOOL_RESERVE
                if workflow.settings.vue_max_tool_calls - state.get("tool_call_count", 0) <= reserve:
                    budget_reason = "NEXT_REPAIR_RESERVE" if has_next_repair else "LOW_TOOL_BUDGET"
                elif loop.get("read_only_batches", 0) >= REPAIR_READ_BATCH_LIMIT:
                    budget_reason = "READ_BATCH_LIMIT"
            stalled = ready and (write_cycle or read_stall or budget_reason is not None)
            if stalled:
                reason = ("REPEATED_SOURCE_SNAPSHOT" if write_cycle else (
                    "REPEATED_IDENTICAL_READS" if read_stall else budget_reason
                ))
                log_budget(state, loop, reason=reason)
            loop.update(pending_tools=[], tool_cursor=0, batch_has_successful_write=False,
                        next_step="finish" if stalled else "model_turn")
        return {"vue_loop": loop}

    def observed(action):
        async def run(state):
            try:
                return await action(state)
            except (ValueError, InputReviewError) as exc:
                if repair_read_guard:
                    code = exc.code if isinstance(exc, InputReviewError) else str(exc).partition(":")[0]
                    await save_repair_failure(workflow, thread_id, state, code)
                raise
        return run

    return {name: observed(action) for name, action in {
        "model_turn": model_turn, "validate_turn": validate_turn,
        "execute_tool": execute_tool, "continue_loop": continue_loop,
    }.items()}


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
