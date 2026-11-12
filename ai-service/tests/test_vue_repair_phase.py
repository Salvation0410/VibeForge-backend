"""修复阶段必须基于本阶段状态，不继承首次生成的规划及重试指令。"""

from copy import deepcopy

import pytest

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.subgraphs.vue import build_vue_tool_subgraph
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint
from test_vue_subgraph import initial, make_workflow


@pytest.mark.parametrize("exists", [False, True])
@pytest.mark.parametrize("previous_repairs", [0, 1])
async def test_repair_phase_discards_previous_feedback_but_keeps_current_retry(
    settings, exists, previous_repairs
):
    class RepairModel(FakeModel):
        async def repair(self, artifact, context):
            self.calls.append(("repair", deepcopy(context)))
            if len(self.calls) == 1:
                return ModelTurn("", vue_output_invalid=True)
            if len(self.calls) == 2:
                return ModelTurn("修复组件调用", tool_calls=[ToolCall("file_write", {
                    "relativeFilePath": "src/App.vue",
                    "content": "<template><main>修复后的测试页面</main></template>",
                })])
            return ModelTurn("修复完成")

    history = [{"toolCallId": "subgraph-test:vue-generate:7", "tool": "file_write",
                "arguments": {"relativeFilePath": "src/App.vue"}, "result": {"ok": True}}]
    context = {
        "prompt": "保留原有功能", "currentArtifact": {"exists": exists},
        "generationPreparation": {"version": "1", "rewriteStatus": "disabled"},
        "toolResults": history,
        "planningFeedback": "上一阶段规划", "batchRetryFeedback": "上一阶段批次",
        "vueFormatFeedback": "上一阶段格式",
    }
    before = deepcopy(context)
    build = {"built": False, "errorCode": "VUE_COMPONENT_CONTRACT_FAILED",
             "message": "src/App.vue: FilterBar.filters"}
    model, gateway = RepairModel(), FakeToolGateway()
    workflow = make_workflow(settings, model, MemoryCheckpoint(), gateway)
    graph = build_vue_tool_subgraph(
        workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {}, node="repair",
    )
    result = await graph.ainvoke({
        **initial(), "context": context, "repair_count": previous_repairs,
        "tool_call_count": 7, "build": build, "validation": {"valid": True, "errors": []},
    })

    contexts = [data for name, data in model.calls if name == "repair"]
    assert len(contexts) == 3
    assert all(data["vueGeneration"]["phase"] == "modify_files" for data in contexts)
    assert all(key not in contexts[0] for key in (
        "planningFeedback", "batchRetryFeedback", "vueFormatFeedback",
    ))
    # 只清除阶段入口的过期反馈，不吞掉本阶段真正发生的格式重试。
    assert "上一轮格式错误" in contexts[1]["vueFormatFeedback"]
    assert contexts[2]["vueFormatFeedback"] == contexts[1]["vueFormatFeedback"]
    assert all("planningFeedback" not in data and "batchRetryFeedback" not in data for data in contexts)
    assert [data["vueGeneration"]["remainingToolCalls"] for data in contexts] == [33, 33, 32]
    assert all(data["currentArtifact"] == before["currentArtifact"] for data in contexts)
    assert all(data["generationPreparation"] == before["generationPreparation"] for data in contexts)
    assert all(data["prompt"] == before["prompt"] and data["build"] == build for data in contexts)
    assert contexts[0]["toolResults"] == history
    assert contexts[-1]["toolResults"][0] == history[0]
    assert len(contexts[-1]["toolResults"]) == 2
    assert result["repair_count"] == previous_repairs + 1
    assert result["tool_call_count"] == 8
    assert result["build"] == {}
    assert len(gateway.calls) == 2
    assert gateway.calls[0]["toolCallId"] == f"subgraph-test:vue-repair:{previous_repairs + 1}:1"
    assert gateway.calls[1]["name"] == "vue_source_snapshot"
    assert context == before
