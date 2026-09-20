from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ai_service.api.schemas import EventError, GenerationEvent, GenerationRequest
from ai_service.config import Settings
from ai_service.infrastructure.checkpoint import CheckpointStore
from ai_service.models.base import GenerationModel
from ai_service.orchestration.cancellation import CancellationRegistry, GenerationCancelled
from ai_service.orchestration.events import EventEmitter


class WorkflowState(TypedDict, total=False):
    app_id: str
    request_id: str
    thread_id: str
    prompt: str
    code_gen_type: str
    conversation: list[dict[str, Any]]
    metadata: dict[str, Any]
    context: dict[str, Any]
    artifact: str
    validation: dict[str, Any]
    build: dict[str, Any]
    quality_passed: bool
    repair_count: int
    tool_call_count: int
    finish_reason: str | None
    token_usage: dict[str, int]
    publish: dict[str, Any]


def _raise_for_incomplete_model_turn(finish_reason: str | None) -> None:
    """在模型因长度或内容策略中止时阻止候选进入校验和发布。"""
    normalized = (finish_reason or "").upper()
    if normalized in {"LENGTH", "MAX_TOKENS"}:
        raise ValueError("MODEL_OUTPUT_TRUNCATED: model output reached its length limit")
    if normalized in {"CONTENT_FILTER", "CONTENT_FILTERED"}:
        raise ValueError("MODEL_OUTPUT_BLOCKED: model output was filtered")


def _after_validation(state: WorkflowState, max_attempts: int) -> str:
    """根据硬校验结果选择构建、质量检查、修复或失败节点。"""
    if not state.get("validation", {}).get("valid", False):
        return "fail" if state.get("repair_count", 0) >= max_attempts else "repair"
    return "build" if state["code_gen_type"] == "VUE_PROJECT" else "review"


def _after_review(state: WorkflowState, max_attempts: int) -> str:
    """质量通过后按生成类型发布，否则进入有限修复或失败。"""
    if state.get("quality_passed", False):
        return "publish" if state["code_gen_type"] == "MULTI_FILE" else "finalize"
    return "fail" if state.get("repair_count", 0) >= max_attempts else "repair"


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
    ):
        self.model = model
        self.tool_gateway = tool_gateway
        self.checkpoint = checkpoint
        self.cancellations = cancellations
        self.settings = settings

    async def run(self, request: GenerationRequest) -> list[GenerationEvent]:
        """完整执行工作流并返回本次请求产生的全部事件。"""
        emitter = EventEmitter(request.request_id)
        await self._execute(request, emitter)
        return emitter.events

    async def stream(self, request: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        """在后台执行工作流，并按产生顺序异步返回事件。"""
        queue: asyncio.Queue[GenerationEvent | None] = asyncio.Queue()
        emitter = EventEmitter(request.request_id, queue)

        async def execute() -> None:
            try:
                await self._execute(request, emitter)
            finally:
                await queue.put(None)

        task = asyncio.create_task(execute())
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

    async def _execute(self, request: GenerationRequest, emitter: EventEmitter) -> None:
        """构造初始状态并执行图，将取消和异常转换为终止事件。"""
        thread_id = f"{request.app_id}:{request.request_id}"
        graph = self._build_graph(emitter, thread_id)
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
            await graph.ainvoke(initial, config={"configurable": {"thread_id": thread_id}})
        except GenerationCancelled:
            await emitter.emit(
                "failed",
                "cancelled",
                data={"status": "cancelled", "threadId": thread_id},
                error=EventError(code="cancelled", message="Generation was cancelled"),
            )
        except Exception as exc:
            await emitter.emit(
                "failed",
                "workflow",
                error=EventError(code="generation_failed", message=str(exc)),
            )

    def _build_graph(self, emitter: EventEmitter, thread_id: str):
        """构建带 checkpoint、工具循环和最多两次修复回环的状态图。"""
        builder = StateGraph(WorkflowState)

        def guarded(name: str, function):
            # 统一处理取消检查、节点状态事件与业务 checkpoint。
            async def node(state: WorkflowState) -> dict[str, Any]:
                self._raise_if_cancelled(thread_id)
                await emitter.node_status(name, "started")
                update = await function(state)
                merged = {**state, **update}
                await self.checkpoint.save(thread_id, self._checkpoint_payload(merged, name))
                await emitter.node_status(name, "completed")
                return update

            return node

        async def input_guard(state: WorkflowState) -> dict[str, Any]:
            if not state["prompt"].strip():
                raise ValueError("Prompt must not be blank")
            return {}

        async def context_prepare(state: WorkflowState) -> dict[str, Any]:
            return {
                "context": {
                    "appId": state["app_id"],
                    "requestId": state["request_id"],
                    "prompt": state["prompt"],
                    "conversation": state.get("conversation", []),
                    "metadata": state.get("metadata", {}),
                }
            }

        async def generate_branch(state: WorkflowState) -> dict[str, Any]:
            branch = state["code_gen_type"]
            turn = await self.model.generate(branch, state["context"])
            if turn.content:
                await emitter.emit("content_delta", f"generate_{branch.lower()}", data={"content": turn.content})
            return {"artifact": turn.content, "finish_reason": turn.finish_reason, "token_usage": turn.token_usage}

        async def vue_agent(state: WorkflowState) -> dict[str, Any]:
            context = dict(state["context"])
            artifact_parts: list[str] = []
            tool_count = 0
            while True:
                self._raise_if_cancelled(thread_id)
                turn = await self.model.generate("VUE_PROJECT", context)
                if turn.content:
                    artifact_parts.append(turn.content)
                    await emitter.emit("content_delta", "vue_agent", data={"content": turn.content})
                if not turn.tool_calls or tool_count >= self.settings.vue_max_tool_calls:
                    break
                for call in turn.tool_calls:
                    if tool_count >= self.settings.vue_max_tool_calls:
                        break
                    tool_count += 1
                    tool_call_id = f"{state['request_id']}:vue:{tool_count}"
                    await emitter.emit(
                        "tool_started",
                        "vue_agent",
                        data={"tool": call.name, "toolCallId": tool_call_id},
                    )
                    result = await self.tool_gateway.invoke(
                        call.name,
                        {**call.arguments, "appId": state["app_id"]},
                        tool_call_id=tool_call_id,
                    )
                    await emitter.emit(
                        "tool_finished",
                        "vue_agent",
                        data={"tool": call.name, "toolCallId": tool_call_id, "result": result},
                    )
                    context.setdefault("toolResults", []).append(
                        {"toolCallId": tool_call_id, "tool": call.name, "result": result}
                    )
            return {"artifact": "\n".join(artifact_parts), "context": context, "tool_call_count": tool_count}

        async def artifact_validation(state: WorkflowState) -> dict[str, Any]:
            _raise_for_incomplete_model_turn(state.get("finish_reason"))
            call_id = f"{state['request_id']}:artifact_validation:{state.get('repair_count', 0)}"
            result = await self._invoke_tool(
                emitter,
                "artifact_validation",
                "artifact_validate",
                {"appId": state["app_id"], "codeGenType": state["code_gen_type"], "artifact": state.get("artifact", "")},
                call_id,
            )
            return {"validation": result}

        async def project_build(state: WorkflowState) -> dict[str, Any]:
            call_id = f"{state['request_id']}:project_build:{state.get('repair_count', 0)}"
            result = await self._invoke_tool(
                emitter,
                "project_build",
                "project_build",
                {"appId": state["app_id"], "codeGenType": state["code_gen_type"]},
                call_id,
            )
            return {"build": result}

        async def quality_review(state: WorkflowState) -> dict[str, Any]:
            if not state.get("validation", {}).get("valid", False):
                return {"quality_passed": False}
            passed = await self.model.review(
                state.get("artifact", ""),
                {**state["context"], "validation": state.get("validation"), "build": state.get("build")},
            )
            return {"quality_passed": passed}

        async def repair(state: WorkflowState) -> dict[str, Any]:
            count = state.get("repair_count", 0) + 1
            turn = await self.model.repair(
                state.get("artifact", ""),
                {**state["context"], "repairCount": count, "validation": state.get("validation"), "build": state.get("build")},
            )
            await emitter.emit("content_delta", "repair", data={"content": turn.content, "repairCount": count})
            return {
                "artifact": turn.content,
                "repair_count": count,
                "finish_reason": turn.finish_reason,
                "token_usage": turn.token_usage,
            }

        async def artifact_publish(state: WorkflowState) -> dict[str, Any]:
            """请求 Spring 重新校验并发布最终多文件候选，失败时中止完成事件。"""
            call_id = f"{state['request_id']}:artifact_publish"
            result = await self._invoke_tool(
                emitter,
                "artifact_publish",
                "artifact_publish",
                {
                    "appId": state["app_id"],
                    "requestId": state["request_id"],
                    "codeGenType": state["code_gen_type"],
                    "artifact": state.get("artifact", ""),
                    "engine": "langgraph",
                    "finishReason": state.get("finish_reason") or "",
                },
                call_id,
            )
            if not result.get("published", False):
                raise ValueError("Spring rejected artifact publication")
            return {"publish": result}

        async def fail_quality(state: WorkflowState) -> dict[str, Any]:
            """在硬校验或质量检查耗尽修复次数后生成明确失败终态。"""
            errors = state.get("validation", {}).get("errors", [])
            raise ValueError(f"Artifact did not pass validation or quality review: {errors}")

        async def finalize(state: WorkflowState) -> dict[str, Any]:
            self._raise_if_cancelled(thread_id)
            await emitter.node_status("finalize", "started")
            await self.checkpoint.save(thread_id, self._checkpoint_payload(state, "finalize"))
            await emitter.node_status("finalize", "completed")
            await emitter.emit(
                "completed",
                "finalize",
                data={
                    "threadId": state["thread_id"],
                    "codeGenType": state["code_gen_type"],
                    "artifact": state.get("artifact", ""),
                    "qualityPassed": state.get("quality_passed", False),
                    "repairCount": state.get("repair_count", 0),
                },
            )
            return {}

        builder.add_node("input_guard", guarded("input_guard", input_guard))
        builder.add_node("context_prepare", guarded("context_prepare", context_prepare))
        builder.add_node("generate_html", guarded("generate_html", generate_branch))
        builder.add_node("generate_multi_file", guarded("generate_multi_file", generate_branch))
        builder.add_node("vue_agent", guarded("vue_agent", vue_agent))
        builder.add_node("artifact_validation", guarded("artifact_validation", artifact_validation))
        builder.add_node("project_build", guarded("project_build", project_build))
        builder.add_node("quality_review", guarded("quality_review", quality_review))
        builder.add_node("repair", guarded("repair", repair))
        builder.add_node("artifact_publish", guarded("artifact_publish", artifact_publish))
        builder.add_node("fail_quality", fail_quality)
        builder.add_node("finalize", finalize)

        builder.add_edge(START, "input_guard")
        builder.add_edge("input_guard", "context_prepare")
        builder.add_conditional_edges(
            "context_prepare",
            lambda state: state["code_gen_type"],
            {"HTML": "generate_html", "MULTI_FILE": "generate_multi_file", "VUE_PROJECT": "vue_agent"},
        )
        for generation_node in ("generate_html", "generate_multi_file", "vue_agent"):
            builder.add_edge(generation_node, "artifact_validation")
        builder.add_conditional_edges(
            "artifact_validation",
            lambda state: _after_validation(state, self.settings.max_repair_attempts),
            {"repair": "repair", "fail": "fail_quality", "build": "project_build", "review": "quality_review"},
        )
        builder.add_edge("project_build", "quality_review")
        builder.add_conditional_edges(
            "quality_review",
            lambda state: _after_review(state, self.settings.max_repair_attempts),
            {"repair": "repair", "fail": "fail_quality", "publish": "artifact_publish", "finalize": "finalize"},
        )
        builder.add_edge("repair", "artifact_validation")
        builder.add_edge("artifact_publish", "finalize")
        builder.add_edge("finalize", END)
        graph_saver = getattr(self.checkpoint, "get_graph_saver", lambda: None)()
        return builder.compile(checkpointer=graph_saver)

    async def _invoke_tool(
        self,
        emitter: EventEmitter,
        node: str,
        name: str,
        arguments: dict[str, Any],
        tool_call_id: str,
    ) -> dict[str, Any]:
        """调用 Spring 工具网关，并在调用前后发送成对事件。"""
        await emitter.emit("tool_started", node, data={"tool": name, "toolCallId": tool_call_id})
        result = await self.tool_gateway.invoke(name, arguments, tool_call_id=tool_call_id)
        await emitter.emit(
            "tool_finished",
            node,
            data={"tool": name, "toolCallId": tool_call_id, "result": result},
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
            "artifact": state.get("artifact"),
            "qualityPassed": state.get("quality_passed"),
            "repairCount": state.get("repair_count", 0),
            "toolCallCount": state.get("tool_call_count", 0),
        }
