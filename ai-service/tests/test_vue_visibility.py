"""使用最终消息可见性判重；被预算省略的补读仍消耗真实工具预算。"""

from copy import deepcopy
from dataclasses import asdict

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.models.vue_context import project_vue_context, visible_file_fingerprints
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.vue_tool_loop import initial_progress, create_vue_loop_nodes
from conftest import FakeModel, MemoryCheckpoint
from test_vue_context import record
from test_vue_prewrite_guard import Gateway
from test_vue_subgraph import make_workflow, initial
from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph
from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint
from ai_service.models.verification_diagnostics import safe_verification_history


def test_visibility_describes_only_complete_final_projected_files():
    history = [record(i, "x" * 4500) for i in range(6)]
    context = {"toolResults": history}
    original = deepcopy(context)
    projected = project_vue_context(context, fits=lambda _: True)
    visible = visible_file_fingerprints(projected)
    assert len(visible) < 6 and "src/5.vue" in visible and "src/0.vue" not in visible
    assert context == original
    assert all(len(value) == 64 for value in visible.values())
    projected = project_vue_context(context, fits=lambda ctx: sum(
        "content" in item["result"] for item in ctx["toolResults"]) <= 1)
    assert set(visible_file_fingerprints(projected)) == {"src/5.vue"}
    for result in ({"content": "partial", "truncated": True}, {"content": "bad", "ok": False}):
        assert visible_file_fingerprints({"toolResults": [{**record(1, ""), "result": result}]}) == {}


@pytest.mark.parametrize("visible", [True, False, None])
async def test_only_visible_unchanged_read_counts_toward_stall(settings, visible):
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint(), Gateway())
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_read_guard=True)
    history = [record(1, "私有完整源码", path="src/App.vue")]
    loop = initial_progress({}, "subgraph-test:vue-repair:1", "2026-11-24T00:00:00+00:00")
    loop.update(visible_files=visible_file_fingerprints({"toolResults": history}) if visible else {},
                pending_tools=[{"name": "file_read", "arguments": {"relativeFilePath": "src/App.vue"}}],
                tool_cursor=0)
    if visible is None:
        loop.pop("visible_files", None)
    state = {**initial(), "vue_loop": loop, "vue_tool_results": history}
    before = deepcopy(state)
    result = await nodes["execute_tool"](state)
    assert result["vue_loop"]["visible_repeat_count"] == (1 if visible else 0)
    assert result["vue_loop"]["omitted_reread_count"] == (0 if visible else 1)
    assert result["tool_call_count"] == 1 and state == before


async def test_same_batch_write_invalidates_visible_read(settings):
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint(), Gateway())
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", "repair")
    history = [record(1, "私有完整源码", path="src/App.vue")]
    loop = initial_progress({}, "subgraph-test:vue-repair:1", "2026-11-24T00:00:00+00:00")
    loop.update(visible_files=visible_file_fingerprints({"toolResults": history}), pending_tools=[
        {"name": "file_write", "arguments": {"relativeFilePath": "src/App.vue", "content": "new"}},
        {"name": "file_read", "arguments": {"relativeFilePath": "src/App.vue"}},
    ])
    state = {**initial(), "vue_loop": loop, "vue_tool_results": history}
    state.update(await nodes["execute_tool"](state))
    assert not state["vue_loop"]["visible_files"]
    state.update(await nodes["execute_tool"](state))
    assert state["vue_loop"]["visible_repeat_count"] == 0


async def test_model_visibility_is_private_not_reused_in_next_payload(settings):
    class Model(FakeModel):
        async def repair(self, artifact, context):
            assert "visible_files" not in str(context)
            return ModelTurn("done", vue_visible_files={"src/App.vue": "a" * 64})
    workflow = make_workflow(settings, Model(), MemoryCheckpoint())
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", "repair")
    loop = initial_progress({}, "subgraph-test:vue-repair:1", "2026-11-24T00:00:00+00:00")
    state = {**initial(), "vue_loop": loop, "vue_tool_results": []}
    update = await nodes["model_turn"](state)
    assert update["vue_loop"]["visible_files"] == {"src/App.vue": "a" * 64}
    assert "vue_visible_files" not in update["vue_loop"]["turn"]


async def test_omitted_six_file_cycle_consumes_budget_without_false_read_stall(settings):
    class LargeGateway(Gateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            return {"content": "x" * 4600} if name == "file_read" else result

    class Cycle(FakeModel):
        async def repair(self, artifact, context):
            projected = project_vue_context(context, fits=lambda _: True)
            reads = context["toolResults"]
            path = f"src/{len(reads) % 6}.vue"
            return ModelTurn("read", tool_calls=[ToolCall("file_read", {"relativeFilePath": path})],
                             vue_visible_files=visible_file_fingerprints(projected))
    checkpoint = MemoryCheckpoint()
    workflow = make_workflow(settings, Cycle(), checkpoint, LargeGateway())
    graph = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    with pytest.raises(ValueError, match="VUE_TOOL_CALL_LIMIT_EXCEEDED"):
        await graph.ainvoke(initial(), {"recursion_limit": 400})
    records = checkpoint.saved["42:subgraph-test"][-1]["verification_history"]
    assert records[-1]["toolCount"] == 40
    assert records[-1]["visibleRepeatCount"] == 0 and records[-1]["omittedRereadCount"] == 34
    assert safe_verification_history(records) == records
    assert PostgresCheckpoint._normalize_state(checkpoint.saved["42:subgraph-test"][-1])["verification_history"] == records
    assert "src/" not in str(records)


async def test_stall_failure_saves_current_counts_and_save_failure_preserves_error(settings):
    from test_vue_prewrite_guard import ReadModel
    class Broken(MemoryCheckpoint):
        async def save(self, *args):
            raise RuntimeError("private-storage-error")
    for checkpoint in (MemoryCheckpoint(), Broken()):
        workflow = make_workflow(settings, ReadModel(), checkpoint, Gateway())
        graph = build_vue_tool_subgraph(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
        # 正常节点也会保存摘要；只在异常出口模拟存储故障。
        if isinstance(checkpoint, Broken):
            nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test",
                                          "repair", repair_read_guard=True)
            loop = initial_progress({}, "subgraph-test:vue-repair:1", "2026-11-24T00:00:00+00:00")
            loop.update(prewrite_warning=True, turn=asdict(ModelTurn("done")))
            with pytest.raises(ValueError, match="VUE_REPAIR_READ_STALLED"):
                await nodes["validate_turn"]({**initial(), "vue_loop": loop, "vue_tool_results": []})
        else:
            with pytest.raises(ValueError, match="VUE_REPAIR_READ_STALLED"):
                await graph.ainvoke(initial(), {"recursion_limit": 300})
            record = checkpoint.saved["42:subgraph-test"][-1]["verification_history"][-1]
            assert record["stage"] == "repair" and record["toolCount"] == 10
            assert record["phaseReadCount"] == 10 and record["phaseWriteCount"] == 0
            assert record["visibleRepeatCount"] == 8 and record["omittedRereadCount"] == 0


@pytest.mark.parametrize("change", [
    {"visibleRepeatCount": True}, {"omittedRereadCount": 41}, {"path": "private"},
    {"stage": "quality_review"}, {"errorCode": "UNKNOWN"},
])
def test_repair_error_history_rejects_untrusted_fields(change):
    from test_verification_history import record
    value = {**record(), "stage": "repair", "verdict": "ERROR", "errorCode": "VUE_REPAIR_READ_STALLED",
             "visibleRepeatCount": 2, "omittedRereadCount": 1}
    assert safe_verification_history([value])
    assert safe_verification_history([{**value, **change}]) is None


async def test_adapter_concurrent_visibility_matches_each_final_message():
    import asyncio
    from types import SimpleNamespace
    from test_openai_compatible import model_with_response
    class Client:
        async def ainvoke(self, messages, **options):
            # 两个请求交错完成；共享客户端不得携带“最后一次可见性”。
            await asyncio.sleep(0)
            return SimpleNamespace(content='{"content":"done","toolCalls":[]}',
                                   response_metadata={}, usage_metadata={})
    model = model_with_response("")
    model._client = Client()
    async def run(index):
        return await model.repair("", {"codeGenType": "VUE_PROJECT", "toolResults": [record(index, str(index))]})
    first, second = await asyncio.gather(run(1), run(2))
    assert set(first.vue_visible_files) == {"src/1.vue"}
    assert set(second.vue_visible_files) == {"src/2.vue"}
