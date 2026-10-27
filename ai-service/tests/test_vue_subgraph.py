"""真实父子 checkpoint 验证，覆盖批次中间恢复和跨阶段预算。"""

import pytest
from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.workflow import GenerationWorkflow
from ai_service.orchestration.subgraphs.vue import vue_recursion_limit
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint
from test_subgraphs import CleaningGraphCheckpoint, request_for


class BatchModel(FakeModel):
    async def generate(self, branch, context):
        self.calls.append(("generate", context))
        return self.turn(context)

    async def repair(self, artifact, context):
        self.calls.append(("repair", context))
        # 修复阶段保留此前工具结果，因此使用单独的阶段计数识别本轮结果。
        results = [item for item in context["toolResults"] if ":vue-repair:" in item["toolCallId"]]
        return self.turn({**context, "toolResults": results})

    def turn(self, context):
        if not context.get("toolResults"):
            return ModelTurn("写入文件", tool_calls=[
                ToolCall("file_write", {"relativeFilePath": path, "content": "<template>ok</template>"})
                for path in ["src/App.vue", "src/Card.vue"]
            ], file_plan=["src/App.vue", "src/Card.vue"], token_usage={"output_tokens": 3})
        return ModelTurn("完成", finish_reason="STOP", token_usage={"output_tokens": 2})


def make_workflow(settings, model, checkpoint, gateway=None, cancellations=None):
    return GenerationWorkflow(model=model, checkpoint=checkpoint,
                              tool_gateway=gateway or FakeToolGateway(),
                              cancellations=cancellations or CancellationRegistry(), settings=settings)


def initial():
    return {"app_id": "42", "request_id": "subgraph-test", "thread_id": "42:subgraph-test",
            "prompt": "页面", "code_gen_type": "VUE_PROJECT", "context": {},
            "repair_count": 0, "tool_call_count": 0, "conversation": [], "metadata": {}}


async def paused_vue_state(graph, config, repair):
    snapshot = await graph.aget_state(config, subgraphs=True)
    while snapshot.tasks:
        snapshots = [task.state for task in snapshot.tasks if task.state is not None]
        if not snapshots:
            break
        snapshot = snapshots[0]
        if "vue_loop" in snapshot.values:
            return snapshot
    pytest.fail(f"未找到暂停的 Vue 子图，repair={repair}")


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("boundary", ["model_turn", "execute_tool"])
async def test_rebuild_parent_resumes_saved_turn_and_batch_without_repeating_work(settings, monkeypatch, repair, boundary):
    import ai_service.orchestration.workflow as workflow_module

    original = workflow_module.build_vue_tool_subgraph

    def interrupted(*args, **kwargs):
        if (kwargs.get("node") == "repair") == repair:
            kwargs["interrupt_after"] = [boundary]
        return original(*args, **kwargs)

    checkpoint = CleaningGraphCheckpoint()
    model = BatchModel(reviews=[False, True] if repair else [True])
    workflow = make_workflow(settings, model, checkpoint)
    emitter = EventEmitter("subgraph-test")
    config = {"configurable": {"thread_id": "42:subgraph-test"},
              "recursion_limit": vue_recursion_limit(settings)}
    monkeypatch.setattr(workflow_module, "build_vue_tool_subgraph", interrupted)
    graph = workflow._build_graph(emitter, "42:subgraph-test", {})
    await graph.ainvoke(initial(), config)
    snapshot = await paused_vue_state(graph, config, repair)
    progress = snapshot.values["vue_loop"]
    assert progress["ordinal"] == (1 if boundary == "execute_tool" else 0)
    assert progress["file_plan"] == (["src/App.vue", "src/Card.vue"] if boundary == "execute_tool" else [])
    assert "vue_loop" not in (await graph.aget_state(config)).values
    calls_before = len([call for call in model.calls if call[0] == ("repair" if repair else "generate")])

    # 重建整个父图且关闭断点，原 saver 与 thread 即可定位保存的子任务。
    monkeypatch.setattr(workflow_module, "build_vue_tool_subgraph", original)
    rebuilt = workflow._build_graph(emitter, "42:subgraph-test", {})
    result = await rebuilt.ainvoke(None, config)
    assert result["tool_call_count"] == (4 if repair else 2)
    assert result["repair_count"] == (1 if repair else 0)
    assert result["token_usage"] == {"output_tokens": 5}
    assert "qualityReview" not in result["context"]
    assert "vue_loop" not in result
    calls_after = len([call for call in model.calls if call[0] == ("repair" if repair else "generate")])
    assert calls_after == calls_before + 1
    prefix = "subgraph-test:vue-repair:1" if repair else "subgraph-test:vue-generate"
    calls = [call for call in workflow.tool_gateway.calls if call["toolCallId"].startswith(prefix)]
    assert [call["toolCallId"] for call in calls] == [f"{prefix}:1", f"{prefix}:2"]
    assert [event.sequence for event in emitter.events] == list(range(1, len(emitter.events) + 1))
    assert sum(event.type == "completed" for event in emitter.events) == 1
    assert all(event.node not in {"model_turn", "execute_tool", "continue_loop"} for event in emitter.events)
    namespaces = {entry.config["configurable"]["checkpoint_ns"] async for entry in checkpoint.saver.alist(config)}
    assert any("vue_repair:" in ns for ns in namespaces) if repair else any("vue_generation:" in ns for ns in namespaces)


@pytest.mark.asyncio
async def test_full_tool_budget_is_not_blocked_by_default_graph_recursion_limit(settings):
    workflow = make_workflow(settings, FakeModel(vue_tool_calls=40), CleaningGraphCheckpoint())
    events = await workflow.run(request_for("VUE_PROJECT"))
    assert events[-1].type == "completed"
    assert len([call for call in workflow.tool_gateway.calls if ":vue-generate:" in call["toolCallId"]]) == 40


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["truncated", "arguments"])
async def test_invalid_batch_never_executes_partial_tools(settings, invalid):
    class InvalidModel(BatchModel):
        def turn(self, context):
            turn = super().turn(context)
            if invalid == "truncated":
                turn.finish_reason = "LENGTH"
            else:
                turn.tool_calls[1] = ToolCall("file_modify", {"relativeFilePath": "src/Card.vue"})
            return turn

    workflow = make_workflow(settings, InvalidModel(), MemoryCheckpoint())
    events = await workflow.run(request_for("VUE_PROJECT"))
    assert events[-1].type == "failed"
    assert not any(":vue-generate:" in call["toolCallId"] for call in workflow.tool_gateway.calls)


@pytest.mark.asyncio
async def test_cancel_after_first_tool_prevents_second_and_cleans_nested_checkpoints(settings):
    cancellations = CancellationRegistry()

    class CancellingGateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            if ":vue-generate:" in kwargs["tool_call_id"]:
                cancellations.cancel("42:subgraph-test")
            return result

    checkpoint = CleaningGraphCheckpoint()
    workflow = make_workflow(settings, BatchModel(), checkpoint, CancellingGateway(), cancellations)
    events = await workflow.run(request_for("VUE_PROJECT"))
    assert events[-1].error.code == "cancelled"
    assert len([call for call in workflow.tool_gateway.calls if ":vue-generate:" in call["toolCallId"]]) == 1
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    assert [entry async for entry in checkpoint.saver.alist(config)] == []


@pytest.mark.asyncio
async def test_repair_cannot_reset_shared_tool_budget(settings):
    workflow = make_workflow(settings.model_copy(update={"vue_max_tool_calls": 3}),
                             BatchModel(reviews=[False]), MemoryCheckpoint())
    events = await workflow.run(request_for("VUE_PROJECT"))
    assert events[-1].error.code == "VUE_TOOL_CALL_LIMIT_EXCEEDED"
    assert not any(":vue-repair:" in call["toolCallId"] for call in workflow.tool_gateway.calls)
