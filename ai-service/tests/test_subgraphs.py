"""验证真实嵌套图的持久化、字段隔离与请求内事件流。"""

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from ai_service.api.schemas import GenerationRequest
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.subgraphs.generation import build_generation_subgraph
from ai_service.orchestration.workflow import GenerationWorkflow
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint


class CleaningGraphCheckpoint(MemoryCheckpoint):
    def __init__(self):
        super().__init__()
        self.saver = InMemorySaver()
        self.namespaces_before_cleanup = set()

    def get_graph_saver(self):
        return self.saver

    async def cleanup_graph(self, thread_id):
        config = {"configurable": {"thread_id": thread_id}}
        self.namespaces_before_cleanup = {
            entry.config["configurable"]["checkpoint_ns"]
            async for entry in self.saver.alist(config)
        }
        await self.saver.adelete_thread(thread_id)


def request_for(branch):
    return GenerationRequest.model_validate({
        "appId": "42", "requestId": "subgraph-test", "prompt": "生成页面",
        "codeGenType": branch,
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("branch,namespace", [
    ("HTML", "html_generation"),
    ("MULTI_FILE", "multi_file_generation"),
    ("VUE_PROJECT", "vue_generation"),
])
async def test_children_inherit_saver_and_thread_cleanup_covers_all_namespaces(settings, branch, namespace):
    checkpoint = CleaningGraphCheckpoint()
    workflow = GenerationWorkflow(
        model=FakeModel(), tool_gateway=FakeToolGateway(), checkpoint=checkpoint,
        cancellations=CancellationRegistry(), settings=settings,
    )
    events = await workflow.run(request_for(branch))

    assert events[-1].type == "completed"
    assert sum(event.type in {"completed", "failed"} for event in events) == 1
    namespaces = checkpoint.namespaces_before_cleanup
    assert "" in namespaces
    assert any(ns.startswith(namespace + ":") for ns in namespaces)
    assert any(ns.startswith("verification_repair:") for ns in namespaces)
    assert all(not ns.startswith(other + ":") for ns in namespaces for other in {
        "html_generation", "multi_file_generation", "vue_generation",
    } - {namespace})
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    assert [entry async for entry in checkpoint.saver.alist(config)] == []


@pytest.mark.asyncio
async def test_generation_output_does_not_expose_request_identity_updates():
    async def generate(state):
        return {"artifact": "candidate", "prompt": "changed", "app_id": "other-app"}

    graph = build_generation_subgraph("generate_html", generate)
    result = await graph.ainvoke({"app_id": "42", "prompt": "original"})
    assert result == {"artifact": "candidate"}


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_or_cancelled_child_cleans_thread_without_publication(settings, cancel):
    checkpoint = CleaningGraphCheckpoint()
    cancellations = CancellationRegistry()
    gateway = FakeToolGateway()

    class FailingModel(FakeModel):
        async def generate(self, branch, context):
            result = await super().generate(branch, context)
            if cancel:
                cancellations.cancel("42:subgraph-test")
            return result

    workflow = GenerationWorkflow(
        model=FailingModel(reviews=[False, False, False]), tool_gateway=gateway,
        checkpoint=checkpoint, cancellations=cancellations, settings=settings,
    )
    events = await workflow.run(request_for("HTML"))
    assert events[-1].type == "failed"
    if cancel:
        assert events[-1].error.code == "cancelled"
    assert sum(event.type in {"failed", "completed"} for event in events) == 1
    assert all(call["name"] != "artifact_publish" for call in gateway.calls)
    assert any(ns.startswith("html_generation:") for ns in checkpoint.namespaces_before_cleanup)
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    assert [entry async for entry in checkpoint.saver.alist(config)] == []


@pytest.mark.asyncio
async def test_subgraph_node_event_is_visible_before_model_finishes(settings):
    entered = asyncio.Event()
    release = asyncio.Event()

    class WaitingModel(FakeModel):
        async def generate(self, branch, context):
            entered.set()
            await release.wait()
            return await super().generate(branch, context)

    workflow = GenerationWorkflow(
        model=WaitingModel(), tool_gateway=FakeToolGateway(), checkpoint=MemoryCheckpoint(),
        cancellations=CancellationRegistry(), settings=settings,
    )
    stream = workflow.stream(request_for("HTML"))
    events = []
    try:
        async with asyncio.timeout(5):
            async for event in stream:
                events.append(event)
                if event.node == "generate_html" and event.data.get("status") == "started":
                    await entered.wait()
                    assert not release.is_set()
                    assert all(item.type != "completed" for item in events)
                    release.set()
    finally:
        release.set()
        await stream.aclose()

    assert events[-1].type == "completed"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert all(event.node not in {"html_generation", "verification_repair"} for event in events)


def test_main_graph_exposes_four_compiled_subgraphs(settings):
    workflow = GenerationWorkflow(
        model=FakeModel(), tool_gateway=FakeToolGateway(), checkpoint=MemoryCheckpoint(),
        cancellations=CancellationRegistry(), settings=settings,
    )
    graph = workflow._build_graph(EventEmitter("test"), "42:test", {})
    assert {name for name, _ in graph.get_subgraphs()} == {
        "html_generation", "multi_file_generation", "vue_generation", "verification_repair",
    }
