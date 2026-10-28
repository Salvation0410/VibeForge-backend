"""验证阶段字段投影、私有历史通道及真实嵌套图的运行配置和恢复。"""

import pytest
from langchain_core.callbacks import AsyncCallbackHandler
from langgraph.config import get_config

from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.subgraphs.generation import build_generation_subgraph
from ai_service.orchestration.subgraphs.verification_repair import build_verification_repair_subgraph
from ai_service.orchestration.subgraphs.vue import build_repair_subgraph
from conftest import FakeModel
from test_subgraphs import CleaningGraphCheckpoint
from test_vue_subgraph import BatchModel, initial, make_workflow, paused_vue_state


def unrelated():
    return {
        "thread_id": "parent-only-thread", "conversation": [{"content": "parent-conversation"}],
        "metadata": {"parent": "metadata"}, "input_review": {"decision": "ALLOW"},
        "publish": {"versionId": "parent-release"},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE"])
async def test_static_branches_filter_raw_child_checkpoint_inputs_and_keep_parent_state(settings, branch):
    checkpoint = CleaningGraphCheckpoint()
    workflow = make_workflow(settings, FakeModel(reviews=[False, True]), checkpoint)
    graph = workflow._build_graph(EventEmitter("subgraph-test"), "42:subgraph-test", {})
    payload = {**initial(), **unrelated(), "code_gen_type": branch}
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    result = await graph.ainvoke(payload, config)
    assert result["repair_count"] == 1
    assert result["artifact"] == f"artifact:{branch}|repaired"
    assert result["metadata"] == payload["metadata"]
    namespaces = set()
    async for entry in checkpoint.saver.alist(config):
        namespace = entry.config["configurable"]["checkpoint_ns"]
        if not namespace:
            continue
        namespaces.add(namespace)
        values = entry.checkpoint["channel_values"]
        assert not set(unrelated()) & values.keys()
        assert not set(unrelated()) & values.get("__start__", {}).keys()
    assert any(ns.startswith("verification_repair:") for ns in namespaces)
    assert any("|repair:" in ns for ns in namespaces)


@pytest.mark.asyncio
async def test_static_generation_has_minimal_input_and_cannot_echo_context_or_phase_results():
    seen = {}

    async def generate(state):
        seen.update(state)
        return {"artifact": "new", "context": {"changed": True}, "validation": {"valid": False},
                "tool_call_count": 999, "repair_count": 999, "app_id": "other"}

    payload = {**initial(), **unrelated(), "artifact": "old", "validation": {"valid": True},
               "build": {"built": True}, "quality_passed": True, "repair_feedback": "previous feedback"}
    result = await build_generation_subgraph("generate_html", generate).ainvoke(payload)
    assert result == {"artifact": "new"}
    assert set(seen) == {"app_id", "request_id", "code_gen_type", "context", "repair_count", "tool_call_count"}


@pytest.mark.asyncio
async def test_verification_filters_parent_fields_and_stale_stage_results_on_entry():
    seen = {}

    async def validate(state):
        seen.update(state)
        return {"validation": {"valid": True}, "publish": {"changed": True}}

    async def review(state):
        return {"quality_passed": True}

    async def unexpected(state):
        pytest.fail("本测试不应进入构建、修复或失败节点")

    graph = build_verification_repair_subgraph({
        "artifact_validation": validate, "project_build": unexpected,
        "quality_review": review, "repair": unexpected, "fail_quality": unexpected,
    }, 2)
    payload = {**initial(), **unrelated(), "code_gen_type": "HTML", "artifact": "candidate",
               "validation": {"valid": False}, "build": {"built": False},
               "quality_passed": False, "repair_feedback": "stale feedback",
               "finish_reason": "STOP", "token_usage": {"output_tokens": 4}}
    result = await graph.ainvoke(payload)
    assert set(seen) == {"app_id", "request_id", "code_gen_type", "context", "prompt",
                         "artifact", "repair_count", "tool_call_count", "finish_reason", "token_usage"}
    assert result["validation"] == {"valid": True}
    assert result["quality_passed"] is True
    assert "publish" not in result
    assert "repair_feedback" not in result


@pytest.mark.asyncio
async def test_repair_dispatch_does_not_return_validation_quality_or_identity():
    seen = {}

    async def repair(state):
        seen.update(state)
        return {"artifact": "fixed", "repair_count": 1, "validation": {"valid": True},
                "quality_passed": True, "app_id": "other", "publish": {"changed": True}}

    payload = {**initial(), **unrelated(), "code_gen_type": "HTML", "artifact": "candidate",
               "validation": {"valid": False}, "build": {}, "quality_passed": False,
               "repair_feedback": "fix it", "prompt": "parent-only prompt"}
    result = await build_repair_subgraph(repair, repair).ainvoke(payload)
    assert result == {"artifact": "fixed", "repair_count": 1, "context": {}, "tool_call_count": 0,
                      "build": {}, "repair_feedback": "fix it"}
    assert not (set(unrelated()) | {"prompt", "quality_passed"}) & seen.keys()


class RecordingCheckpoint(CleaningGraphCheckpoint):
    def __init__(self):
        super().__init__()
        self.writes = []
        original = self.saver.aput_writes

        async def record(config, writes, task_id, task_path=""):
            self.writes.append((config["configurable"].get("checkpoint_ns", ""), dict(writes)))
            return await original(config, writes, task_id, task_path)

        self.saver.aput_writes = record


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
async def test_private_history_survives_rebuild_without_duplication_or_parent_field_leaks(settings, monkeypatch, repair):
    import ai_service.orchestration.workflow as workflow_module

    original = workflow_module.build_vue_tool_subgraph

    def interrupted(*args, **kwargs):
        if (kwargs.get("node") == "repair") == repair:
            kwargs["interrupt_after"] = ["execute_tool"]
        return original(*args, **kwargs)

    checkpoint = RecordingCheckpoint()
    model = BatchModel(reviews=[False, True] if repair else [True])
    workflow = make_workflow(settings, model, checkpoint)
    emitter = EventEmitter("subgraph-test")
    config = {"configurable": {"thread_id": "42:subgraph-test"}}
    monkeypatch.setattr(workflow_module, "build_vue_tool_subgraph", interrupted)
    graph = workflow._build_graph(emitter, "42:subgraph-test", {})
    payload = {**initial(), **unrelated()}
    await graph.ainvoke(payload, config)
    snapshot = await paused_vue_state(graph, config, repair)
    assert not set(unrelated()) & snapshot.values.keys()
    assert "toolResults" not in snapshot.values["context"]
    assert len(snapshot.values["vue_tool_results"]) == (3 if repair else 1)
    if not repair:
        assert not {"validation", "build", "repair_feedback", "artifact"} & snapshot.values.keys()
    root_before = (await graph.aget_state(config)).values
    assert "vue_tool_results" not in root_before

    monkeypatch.setattr(workflow_module, "build_vue_tool_subgraph", original)
    rebuilt = workflow._build_graph(emitter, "42:subgraph-test", {})
    result = await rebuilt.ainvoke(None, config)
    history = result["context"]["toolResults"]
    assert len(history) == (4 if repair else 2)
    assert len({item["toolCallId"] for item in history}) == len(history)
    assert all(set(item["arguments"]) <= {"relativeFilePath", "relativeDirPath", "query"} for item in history)
    assert any(item["arguments"].get("relativeFilePath") for item in history)
    assert result["metadata"] == payload["metadata"]
    assert result["conversation"] == payload["conversation"]
    assert result["publish"] == payload["publish"]
    assert "vue_tool_results" not in result
    assert "vue_loop" not in result

    for entry in [entry async for entry in checkpoint.saver.alist(config)]:
        namespace = entry.config["configurable"]["checkpoint_ns"]
        if namespace:
            assert not set(unrelated()) & entry.checkpoint["channel_values"].keys()
            entry_input = entry.checkpoint["channel_values"].get("__start__", {})
            assert not set(unrelated()) & entry_input.keys()
    target_ns = snapshot.config["configurable"]["checkpoint_ns"]
    writes = [write for ns, write in checkpoint.writes if ns == target_ns]
    # 初始化、两次工具执行及出口清空各一次；模型、校验和继续均不复制历史通道。
    assert sum("vue_tool_results" in write for write in writes) == 4
    final_child = await checkpoint.saver.aget_tuple({
        "configurable": {"thread_id": "42:subgraph-test", "checkpoint_ns": target_ns},
    })
    assert final_child.checkpoint["channel_values"]["vue_tool_results"] == []
    assert final_child.checkpoint["channel_values"]["vue_loop"]["content_parts"] == []
    assert len(final_child.checkpoint["channel_values"]["context"]["toolResults"]) == len(history)
    # 输入映射、初始化和出口各一次；模型、校验与单工具执行均不改写稳定上下文。
    assert sum("context" in write for write in writes) == 3


@pytest.mark.asyncio
async def test_nested_graph_discovery_and_runtime_config_callbacks_survive_schema_projection(settings):
    configurations = []

    class ConfiguredModel(BatchModel):
        async def generate(self, branch, context):
            config = get_config()
            configurations.append(config)
            return await super().generate(branch, context)

    class RecordingCallbacks(AsyncCallbackHandler):
        def __init__(self):
            self.seen_tags = []

        async def on_chain_start(self, serialized, inputs, *, run_id, tags=None, **kwargs):
            self.seen_tags.append(tags or [])

    callbacks = RecordingCallbacks()
    workflow = make_workflow(settings, ConfiguredModel(), CleaningGraphCheckpoint())
    graph = workflow._build_graph(EventEmitter("subgraph-test"), "42:subgraph-test", {})
    names = {name for name, _ in graph.get_subgraphs(recurse=True)}
    assert {"html_generation", "multi_file_generation", "vue_generation", "verification_repair"} <= names
    assert any("vue_repair" in name for name in names)
    result = await graph.ainvoke(initial(), config={
        "configurable": {"thread_id": "42:subgraph-test", "test_marker": "kept"},
        "tags": ["state-boundaries"], "callbacks": [callbacks],
    })
    assert result["tool_call_count"] == 2
    assert len(configurations) == 2
    assert all(config["configurable"]["test_marker"] == "kept" for config in configurations)
    assert all("vue_generation:" in config["configurable"]["checkpoint_ns"] for config in configurations)
    assert callbacks.seen_tags and all("state-boundaries" in tags for tags in callbacks.seen_tags)
