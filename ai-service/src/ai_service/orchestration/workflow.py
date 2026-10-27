from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

from langgraph.graph import END, START, StateGraph
from langsmith import tracing_context

from ai_service.api.schemas import EventError, GenerationEvent, GenerationRequest
from ai_service.config import Settings
from ai_service.infrastructure.checkpoint import CheckpointStore
from ai_service.models.base import GenerationModel
from ai_service.orchestration.cancellation import CancellationRegistry, GenerationCancelled
from ai_service.orchestration.active_generations import ActiveGenerationRegistry
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.input_review import InputReviewer
from ai_service.models.input_review import InputReviewError
from ai_service.infrastructure.langsmith_tracing import LangSmithTracer

from ai_service.orchestration.state import WorkflowState
# 保留历史内部导入入口；路由实现集中在独立模块。
from ai_service.orchestration.routing import _after_validation, _after_build, _after_review
from ai_service.orchestration.subgraphs.generation import build_generation_subgraph
from ai_service.orchestration.subgraphs.verification_repair import build_verification_repair_subgraph
from ai_service.orchestration.subgraphs.vue import build_repair_subgraph, build_vue_tool_subgraph, vue_recursion_limit
from ai_service.orchestration.nodes.common import guarded as guard_node
from ai_service.orchestration.nodes.input import create_input_nodes
from ai_service.orchestration.nodes.generation import create_generation_nodes
from ai_service.orchestration.nodes.verification import create_verification_nodes
from ai_service.orchestration.nodes.publication import create_publication_nodes
from ai_service.orchestration.vue_tool_loop import run_vue_tool_loop

logger = logging.getLogger(__name__)


def _stable_error_code(exc: Exception) -> str:
    """从业务异常前缀提取稳定错误码，未知异常统一归为生成失败。"""
    prefix = str(exc).partition(":")[0].strip()
    if prefix and prefix == prefix.upper() and prefix.replace("_", "").isalnum():
        return prefix
    return "GENERATION_FAILED"


class GenerationWorkflow:
    """组织代码生成、工具调用、构建、质量检查和有限修复的 LangGraph 工作流。"""
    def __init__(
        self,
        *,
        model: GenerationModel,
        tool_gateway: Any,
        checkpoint: CheckpointStore,
        cancellations: CancellationRegistry,
        settings: Settings,
        active_generations: ActiveGenerationRegistry | None = None,
        tracer: LangSmithTracer | None = None,
    ):
        self.model = model
        self.tool_gateway = tool_gateway
        self.checkpoint = checkpoint
        self.cancellations = cancellations
        self.active_generations = active_generations or ActiveGenerationRegistry()
        self.settings = settings
        self.input_reviewer = InputReviewer(model, settings)
        self.tracer = tracer

    async def run(self, request: GenerationRequest) -> list[GenerationEvent]:
        """完整执行工作流并返回本次请求产生的全部事件。"""
        emitter = EventEmitter(request.request_id)
        await self._execute(request, emitter)
        return emitter.events

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        """在后台执行工作流，并按产生顺序异步返回事件。"""
        queue: asyncio.Queue[GenerationEvent | None] = asyncio.Queue()
        emitter = EventEmitter(request.request_id, queue)
        thread_id = f"{request.app_id}:{request.request_id}"

        async def execute() -> None:
            try:
                await self._execute(request, emitter, convert_task_cancel=True)
            finally:
                await queue.put(None)

        task = asyncio.create_task(execute())
        self.active_generations.register(thread_id, task)
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
            await task
        finally:
            if not task.done():
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.active_generations.clear(thread_id, task)

    async def _execute(
        self,
        request: GenerationRequest,
        emitter: EventEmitter,
        *,
        convert_task_cancel: bool = False,
    ) -> None:
        """构造初始状态并执行图，将取消和异常转换为终止事件。"""
        thread_id = f"{request.app_id}:{request.request_id}"
        terminal: dict[str, Any] = {"published": False, "completed": False}
        started_at = datetime.now(UTC)
        trace_status = "failed"
        trace_error = None
        graph = self._build_graph(emitter, thread_id, terminal)
        initial: WorkflowState = {
            "app_id": request.app_id,
            "request_id": request.request_id,
            "thread_id": thread_id,
            "prompt": request.prompt,
            "code_gen_type": request.code_gen_type.value,
            "conversation": [item.model_dump() for item in request.conversation],
            "metadata": request.metadata,
            "repair_count": 0,
            "tool_call_count": 0,
        }
        try:
            with tracing_context(enabled=False):
                await graph.ainvoke(initial, config={"configurable": {"thread_id": thread_id}})
            trace_status = "completed" if terminal.get("completed") else "failed"
        except GenerationCancelled:
            trace_status, trace_error = "cancelled", "cancelled"
            if not await self._complete_committed_publication(emitter, terminal):
                await emitter.emit(
                    "failed",
                    "cancelled",
                    data={"status": "cancelled", "threadId": thread_id},
                    error=EventError(code="cancelled", message="Generation was cancelled"),
                )
        except asyncio.CancelledError:
            trace_status, trace_error = "cancelled", "cancelled"
            if not convert_task_cancel:
                raise
            if not await self._complete_committed_publication(emitter, terminal):
                await emitter.emit(
                    "failed",
                    "cancelled",
                    data={"status": "cancelled", "threadId": thread_id},
                    error=EventError(code="cancelled", message="Generation was cancelled"),
                )
        except Exception as exc:
            trace_error = _stable_error_code(exc)
            if not await self._complete_committed_publication(emitter, terminal):
                await emitter.emit(
                    "failed",
                    "workflow",
                    error=EventError(code=_stable_error_code(exc),
                                     message=exc.user_message if isinstance(exc, InputReviewError) else str(exc)),
                )
        finally:
            if self.tracer is not None:
                if emitter.events and emitter.events[-1].error is not None:
                    trace_error = emitter.events[-1].error.code
                await self.tracer.record(
                    name="generation",
                    metadata={
                        "request_id": request.request_id,
                        "app_id": request.app_id,
                        "code_gen_type": request.code_gen_type.value,
                        "repair_count": terminal.get("repair_count", 0),
                        "tool_call_count": terminal.get("tool_call_count", 0),
                    },
                    status="completed" if terminal.get("completed") else trace_status,
                    error_code=None if terminal.get("completed") else trace_error,
                    started_at=started_at,
                )
            # checkpoint 清理属于终态维护动作，失败不能覆盖业务终态。
            try:
                await self.checkpoint.cleanup_graph(thread_id)
            except Exception as exc:
                logger.warning("Failed to clean graph checkpoint for thread %s: %s", thread_id, exc)
            # 请求进入明确终态或任务被取消后立即释放注册表标记，避免唯一 requestId 长期累积。
            self.cancellations.clear(thread_id)

    def _build_graph(self, emitter: EventEmitter, thread_id: str, terminal: dict[str, Any]):
        """主图仅调度输入、生成子图、验证修复子图、发布和成功终态。"""
        builder = StateGraph(WorkflowState)
        input_nodes = create_input_nodes(self, emitter, thread_id, terminal)
        generation_nodes = create_generation_nodes(self, emitter, thread_id, terminal)
        verification_nodes = create_verification_nodes(self, emitter, thread_id, terminal)
        publication_nodes = create_publication_nodes(self, emitter, thread_id, terminal)

        def guarded(name, function):
            return guard_node(self, emitter, thread_id, terminal, name, function)

        for name, node in input_nodes.items():
            builder.add_node(name, guarded(name, node))
        # 各生成实例拥有父图分配的命名空间，事件仍沿用旧节点名称。
        for parent_name, child_name, action in (
            ("html_generation", "generate_html", "generate_branch"),
            ("multi_file_generation", "generate_multi_file", "generate_branch"),
        ):
            builder.add_node(parent_name, build_generation_subgraph(
                child_name, guarded(child_name, generation_nodes[action]),
            ))
        builder.add_node("vue_generation", build_vue_tool_subgraph(self, emitter, thread_id, terminal))
        repair = build_repair_subgraph(
            guarded("repair", verification_nodes["repair"]),
            build_vue_tool_subgraph(self, emitter, thread_id, terminal, node="repair"),
        )
        builder.add_node("verification_repair", build_verification_repair_subgraph(
            {name: repair if name == "repair" else guarded(name, node) if name != "fail_quality" else node
             for name, node in verification_nodes.items()},
            self.settings.max_repair_attempts,
        ))
        builder.add_node("artifact_publish", publication_nodes["artifact_publish_node"])
        builder.add_node("finalize", publication_nodes["finalize"])
        builder.add_edge(START, "input_guard")
        builder.add_edge("input_guard", "context_prepare")
        builder.add_conditional_edges(
            "context_prepare",
            lambda state: state["code_gen_type"],
            {"HTML": "html_generation", "MULTI_FILE": "multi_file_generation", "VUE_PROJECT": "vue_generation"},
        )
        for name in ("html_generation", "multi_file_generation", "vue_generation"):
            builder.add_edge(name, "verification_repair")
        # 子图只有质量通过才能正常返回；失败沿异常通道交给请求级执行器。
        builder.add_conditional_edges(
            "verification_repair",
            lambda state: "publish" if state["code_gen_type"] in {"HTML", "MULTI_FILE"} else "finalize",
            {"publish": "artifact_publish", "finalize": "finalize"},
        )
        builder.add_edge("artifact_publish", "finalize")
        builder.add_edge("finalize", END)
        graph_saver = getattr(self.checkpoint, "get_graph_saver", lambda: None)()
        # 在图对象上绑定默认预算，API、Studio 和内部直接调用使用同一上限。
        return builder.compile(checkpointer=graph_saver).with_config(
            {"recursion_limit": vue_recursion_limit(self.settings)}
        )

    async def _run_vue_tool_loop(self, **kwargs) -> dict[str, Any]:
        """保留内部调用入口，循环实现集中在独立模块。"""
        return await run_vue_tool_loop(self, **kwargs)

    async def _complete_committed_publication(
        self, emitter: EventEmitter, terminal: dict[str, Any]
    ) -> bool:
        """已提交文件后吞掉外围持久化异常，并确保最多补发一次 completed 终态。"""
        if not terminal.get("published", False):
            return False
        if not terminal.get("completed", False):
            await emitter.emit("completed", "finalize", data=terminal.get("data", {}))
            terminal["completed"] = True
        return True

    async def _invoke_tool(
        self,
        emitter: EventEmitter,
        node: str,
        name: str,
        arguments: dict[str, Any],
        app_id: str,
        request_id: str,
        tool_call_id: str,
        on_result: Callable[[dict[str, Any]], None] | None = None,
        event_result: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """调用 Spring 工具网关，并支持记录权威结果及映射事件可见结果。"""
        await emitter.emit("tool_started", node, data={"tool": name, "toolCallId": tool_call_id})
        result = await self.tool_gateway.invoke(
            name,
            arguments,
            app_id=app_id,
            request_id=request_id,
            tool_call_id=tool_call_id,
        )
        if on_result is not None:
            on_result(result)
        await emitter.emit(
            "tool_finished",
            node,
            data={
                "tool": name,
                "toolCallId": tool_call_id,
                "arguments": {key: value for key, value in arguments.items()
                              if key in {"relativeFilePath", "relativeDirPath", "query"}},
                "result": event_result(result) if event_result is not None else result,
            },
        )
        return result

    def _raise_if_cancelled(self, thread_id: str) -> None:
        """在节点边界检测取消信号并中断当前图执行。"""
        if self.cancellations.is_cancelled(thread_id):
            raise GenerationCancelled()

    @staticmethod
    def _checkpoint_payload(state: WorkflowState, node: str) -> dict[str, Any]:
        """提取可观测的精简状态，避免将完整上下文写入业务 checkpoint。"""
        return {
            "node": node,
            "requestId": state.get("request_id"),
            "appId": state.get("app_id"),
            "codeGenType": state.get("code_gen_type"),
            "qualityPassed": state.get("quality_passed"),
            "repairCount": state.get("repair_count", 0),
            "toolCallCount": state.get("tool_call_count", 0),
        }
