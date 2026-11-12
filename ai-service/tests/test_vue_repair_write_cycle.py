"""源码集合重复只结束修复工具阶段，最终结果仍由构建和审查决定。"""

from copy import deepcopy
import asyncio

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.cancellation import GenerationCancelled
from ai_service.orchestration.repair_progress import vue_snapshot_fingerprint
from ai_service.orchestration.vue_tool_loop import create_vue_loop_nodes, initial_progress
from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint
from test_subgraphs import request_for, CleaningGraphCheckpoint
from test_vue_subgraph import initial, make_workflow, paused_vue_state


def snapshot(files):
    return {"files": [{"path": path, "content": content, "truncated": False}
                      for path, content in files.items()],
            "eligibleFileCount": len(files), "includedFileCount": len(files),
            "omittedFileCount": 0, "truncated": False}


class CycleModel(FakeModel):
    async def repair(self, artifact, context):
        self.calls.append(("repair", deepcopy(context)))
        phase = context["repairCount"]
        count = sum(r["toolCallId"].startswith(f"subgraph-test:vue-repair:{phase}:")
                    for r in context["toolResults"])
        return ModelTurn("修改组件", tool_calls=[ToolCall("file_write", {
            "relativeFilePath": "src/App.vue", "content": "A" if count % 2 == 0 else "B",
        })])


class SourceGateway(FakeToolGateway):
    def __init__(self, *, fixed=True):
        super().__init__()
        self.files = {"src/App.vue": "初始内容"}
        self.fixed = fixed

    async def invoke(self, name, arguments, **kwargs):
        result = await super().invoke(name, arguments, **kwargs)
        if name == "file_write":
            self.files[arguments["relativeFilePath"]] = arguments["content"]
        if name == "vue_source_snapshot":
            return snapshot(self.files)
        if name == "project_build":
            count = sum(c["name"] == name for c in self.calls)
            if count == 1 or not self.fixed:
                return {"built": False, "errorCode": "VUE_COMPONENT_CONTRACT_FAILED",
                        "message": "组件缺少属性"}
        return result


@pytest.mark.parametrize("fixed", [True, False])
async def test_cycle_returns_to_real_graph_validation_without_claiming_success(settings, fixed):
    model, gateway = CycleModel(), SourceGateway(fixed=fixed)
    events = await make_workflow(settings, model, MemoryCheckpoint(), gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == ("completed" if fixed else "failed")
    if not fixed:
        assert events[-1].error.code == "VUE_COMPONENT_CONTRACT_FAILED"
    assert sum(e.type in {"completed", "failed"} for e in events) == 1
    assert sum(c["name"] == "project_build" for c in gateway.calls) == (2 if fixed else 3)
    assert sum(c[0] == "review" for c in model.calls) == (1 if fixed else 0)
    writes = [c for c in gateway.calls if c["name"] == "file_write" and ":vue-repair:" in c["toolCallId"]]
    assert len(writes) == (3 if fixed else 6)
    scans = [c for c in gateway.calls if ":source-snapshot:" in c["toolCallId"]]
    assert len(scans) == len(writes)
    assert len({c["toolCallId"] for c in scans}) == len(scans)
    for event in events:
        if event.type == "tool_finished" and event.data.get("tool") == "vue_source_snapshot":
            assert set(event.data["result"]) == {"eligibleFileCount", "includedFileCount",
                                                   "omittedFileCount", "truncated"}
    for name, context in model.calls:
        if name == "repair":
            assert "write_fingerprints" not in context
            assert all(r["tool"] != "vue_source_snapshot" for r in context["toolResults"])


async def test_other_file_progress_prevents_single_component_reversion_exit(settings):
    class Model(CycleModel):
        async def repair(self, artifact, context):
            if len(self.calls) == 3:
                return ModelTurn("完成")
            turn = await super().repair(artifact, context)
            # 同一组件固定为 A，但另一文件每批继续改变；不能用局部相等提前退出。
            turn.tool_calls[0].arguments["content"] = "A"
            turn.tool_calls.append(ToolCall("file_write", {
                "relativeFilePath": "src/Other.vue", "content": str(len(self.calls)),
            }))
            return turn

    model, gateway = Model(), SourceGateway()
    graph = build_vue_tool_subgraph(make_workflow(settings, model, MemoryCheckpoint(), gateway),
                                   EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair")
    result = await graph.ainvoke(initial())
    assert result["tool_call_count"] == 6
    assert len(model.calls) == 3
    assert [c["name"] for c in gateway.calls] == ["file_write", "file_write", "vue_source_snapshot"] * 3
    assert "vue_loop" not in result


def batch_state():
    loop = initial_progress({}, "subgraph-test:vue-repair:1", "2026-11-12T00:00:00+00:00")
    loop.update(pending_tools=[{"name": "file_write", "arguments": {
        "relativeFilePath": "src/App.vue", "content": "A",
    }}], tool_cursor=1, ordinal=1, batch_has_successful_write=True,
                write_fingerprints=[vue_snapshot_fingerprint(snapshot({"src/App.vue": "A"}))])
    return {**initial(), "vue_loop": loop, "vue_tool_results": [], "tool_call_count": 39}


@pytest.mark.parametrize("case,expected,scans", [
    ("repeat", "finish", 1), ("pending_file", "model_turn", 1),
    ("pending_image", "model_turn", 1), ("partial_batch", "execute_tool", 0),
    ("no_write", "model_turn", 0), ("disabled", "model_turn", 0),
])
async def test_guard_requires_complete_written_batch_and_satisfied_requirements(settings, case, expected, scans):
    gateway = FakeToolGateway(vue_source_snapshot=snapshot({"src/App.vue": "A"}))
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway)
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_write_guard=case != "disabled")
    state = batch_state()
    if case == "pending_file":
        state["vue_loop"]["file_plan"] = ["src/Other.vue"]
    if case == "pending_image":
        state["vue_loop"]["image_used"] = False
    if case == "partial_batch":
        state["vue_loop"]["pending_tools"].append({"name": "file_delete"})
    if case == "no_write":
        state["vue_loop"]["batch_has_successful_write"] = False
    before = deepcopy(state)
    result = await nodes["continue_loop"](state)
    assert result["vue_loop"]["next_step"] == expected
    assert len(gateway.calls) == scans
    assert state == before
    assert set(result) == {"vue_loop"}
    assert len(result["vue_loop"]["write_fingerprints"]) == 1


@pytest.mark.parametrize("uncertain", [
    {}, snapshot({}), {**snapshot({"src/App.vue": "A"}), "truncated": True},
    {**snapshot({"src/App.vue": "A"}), "omittedFileCount": 1},
    {**snapshot({"src/App.vue": "A"}), "includedFileCount": True},
    {**snapshot({"src/App.vue": "A"}), "files": [{"path": "src/App.vue", "content": "A", "truncated": True}]},
])
async def test_incomplete_snapshot_never_yields_or_erases_valid_history(settings, uncertain):
    gateway = FakeToolGateway(vue_source_snapshot=uncertain)
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway)
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_write_guard=True)
    state = batch_state()
    result = await nodes["continue_loop"](state)
    assert result["vue_loop"]["next_step"] == "model_turn"
    assert result["vue_loop"]["write_fingerprints"] == state["vue_loop"]["write_fingerprints"]


@pytest.mark.parametrize("files", [
    {"src/App.vue": "B"}, {"src/App.vue": "A", "src/New.vue": "新文件"},
    {"src/Renamed.vue": "A"},
])
async def test_changed_content_and_file_set_are_new_evidence(settings, files):
    gateway = FakeToolGateway(vue_source_snapshot=snapshot(files))
    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway),
                                  EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_write_guard=True)
    result = await nodes["continue_loop"](batch_state())
    assert result["vue_loop"]["next_step"] == "model_turn"
    assert len(result["vue_loop"]["write_fingerprints"]) == 2


@pytest.mark.parametrize("tool", ["file_write", "file_modify", "file_delete", "file_read"])
@pytest.mark.parametrize("ok", [True, False, None, 1])
async def test_only_explicitly_successful_write_arms_snapshot(settings, tool, ok):
    class Gateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            await super().invoke(name, arguments, **kwargs)
            return {} if ok is None else {"ok": ok}

    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint(), Gateway()),
                                  EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_write_guard=True)
    state = batch_state()
    state["vue_loop"].update(tool_cursor=0, batch_has_successful_write=False)
    state["vue_loop"]["pending_tools"][0]["name"] = tool
    result = await nodes["execute_tool"](state)
    assert result["vue_loop"]["batch_has_successful_write"] == (
        ok is True and tool in {"file_write", "file_modify"})
    assert result["tool_call_count"] == 40


@pytest.mark.parametrize("failure", [RuntimeError("SNAPSHOT_UNAVAILABLE"),
                                     GenerationCancelled(), asyncio.CancelledError()])
async def test_snapshot_failure_and_cancellation_propagate_without_model_retry(settings, failure):
    class Gateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            await super().invoke(name, arguments, **kwargs)
            raise failure

    model, gateway = FakeModel(), Gateway()
    workflow = make_workflow(settings, model, MemoryCheckpoint(), gateway)
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_write_guard=True)
    with pytest.raises(type(failure)):
        await nodes["continue_loop"](batch_state())
    assert len(gateway.calls) == 1
    assert not model.calls


@pytest.mark.parametrize("when", ["before", "during"])
async def test_cooperative_cancel_wins_over_duplicate_snapshot(settings, when):
    class Gateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            workflow.cancellations.cancel("42:subgraph-test")
            return result

    gateway = Gateway(vue_source_snapshot=snapshot({"src/App.vue": "A"}))
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway)
    if when == "before":
        workflow.cancellations.cancel("42:subgraph-test")
    nodes = create_vue_loop_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_write_guard=True)
    with pytest.raises(GenerationCancelled):
        await nodes["continue_loop"](batch_state())
    assert len(gateway.calls) == (0 if when == "before" else 1)


@pytest.mark.parametrize("boundary", ["execute_tool", "continue_loop"])
async def test_parent_checkpoint_resume_keeps_batch_and_phase_fingerprints(settings, monkeypatch, boundary):
    import ai_service.orchestration.workflow as module

    original = module.build_vue_tool_subgraph

    def interrupted(*args, **kwargs):
        if kwargs.get("node") == "repair":
            kwargs["interrupt_after"] = [boundary]
        return original(*args, **kwargs)

    model, gateway, checkpoint = CycleModel(), SourceGateway(), CleaningGraphCheckpoint()
    workflow = make_workflow(settings, model, checkpoint, gateway)
    emitter = EventEmitter("subgraph-test")
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    monkeypatch.setattr(module, "build_vue_tool_subgraph", interrupted)
    graph = workflow._build_graph(emitter, "42:subgraph-test", {})
    await graph.ainvoke(initial(), config)
    saved = await paused_vue_state(graph, config, True)
    assert saved.values["vue_loop"]["ordinal"] == 1
    assert len(saved.values["vue_loop"]["write_fingerprints"]) == (1 if boundary == "continue_loop" else 0)
    monkeypatch.setattr(module, "build_vue_tool_subgraph", original)
    result = await workflow._build_graph(emitter, "42:subgraph-test", {}).ainvoke(None, config)
    assert result["tool_call_count"] == 3
    assert result["repair_count"] == 1
    assert "vue_loop" not in result
    assert sum(e.type == "completed" for e in emitter.events) == 1
    assert sum(c["name"] == "file_write" for c in gateway.calls) == 3
    assert sum(":source-snapshot:" in c["toolCallId"] for c in gateway.calls) == 3


@pytest.mark.parametrize("node,legacy,expected_writes", [
    ("repair", False, 2), ("vue_agent", False, 3), ("repair", True, 3),
])
async def test_identical_writes_yield_only_in_formal_repair(settings, node, legacy, expected_writes):
    class Model(FakeModel):
        async def step(self):
            self.calls.append(("step", {}))
            if len(self.calls) > 3:
                return ModelTurn("完成")
            return ModelTurn("写入", tool_calls=[ToolCall("file_write", {
                "relativeFilePath": "src/App.vue", "content": "不变",
            })])

        async def repair(self, artifact, context):
            return await self.step()

        async def generate(self, branch, context):
            return await self.step()

    model, gateway = Model(), SourceGateway()
    graph = build_vue_tool_subgraph(make_workflow(settings, model, MemoryCheckpoint(), gateway),
                                   EventEmitter("subgraph-test"), "42:subgraph-test", {},
                                   node=node, legacy=legacy)
    result = await graph.ainvoke({**initial(), "tool_call_count": 37})
    assert result["tool_call_count"] == 37 + expected_writes
    assert sum(c["name"] == "vue_source_snapshot" for c in gateway.calls) == (2 if expected_writes == 2 else 0)


async def test_successful_write_then_delete_is_scanned_after_deletion(settings):
    class Gateway(SourceGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            if name == "file_delete":
                del self.files[arguments["relativeFilePath"]]
            return result

    gateway = Gateway()
    gateway.files = {"src/App.vue": "A", "src/Removed.vue": "待删除"}
    nodes = create_vue_loop_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway),
                                  EventEmitter("subgraph-test"), "42:subgraph-test",
                                  "repair", repair_write_guard=True)
    state = batch_state()
    state["vue_loop"]["write_fingerprints"] = [vue_snapshot_fingerprint(snapshot(gateway.files))]
    state["vue_loop"]["pending_tools"].append({"name": "file_delete", "arguments": {
        "relativeFilePath": "src/Removed.vue",
    }})
    state.update(await nodes["continue_loop"](state))
    assert state["vue_loop"]["next_step"] == "execute_tool"
    assert not gateway.calls
    state.update(await nodes["execute_tool"](state))
    result = await nodes["continue_loop"](state)
    assert result["vue_loop"]["next_step"] == "model_turn"
    assert len(result["vue_loop"]["write_fingerprints"]) == 2
    assert [c["name"] for c in gateway.calls] == ["file_delete", "vue_source_snapshot"]
