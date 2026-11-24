"""可信硬构建问题只在安全清单内组织修复，不靠自由报错推断路径。"""

from copy import deepcopy
from dataclasses import asdict

import pytest

from ai_service.models.base import ModelTurn
from ai_service.models.vue_build_repair import component_repair_issues
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.nodes.verification import create_verification_nodes
from ai_service.orchestration.vue_repair_workset import initial_repair_work, prepare_work_turn, require_write_after_reads
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint
from test_vue_subgraph import initial, make_workflow
from test_subgraphs import request_for


ISSUE = {"file": "src/App.vue", "component": "SalesTrend", "prop": "data"}
BUILD = {"built": False, "errorCode": "VUE_COMPONENT_CONTRACT_FAILED", "message": "固定错误",
         "componentIssues": [ISSUE]}
SNAPSHOT = {"files": [{"path": path, "content": "<template/>", "truncated": False}
                      for path in ["src/App.vue", "src/SalesTrend.vue"]],
            "eligibleFileCount": 2, "includedFileCount": 2, "omittedFileCount": 0, "truncated": False}


def test_component_issues_require_structured_evidence_and_complete_safe_manifest():
    issues, manifest = component_repair_issues(BUILD, SNAPSHOT)
    assert len(issues) == 1 and issues[0]["code"] == "TECHNICAL_ERROR"
    assert "SalesTrend" in issues[0]["evidence"] and "data" in issues[0]["evidence"]
    assert manifest == ["src/App.vue", "src/SalesTrend.vue"]
    assert initial_repair_work(issues, manifest)


@pytest.mark.parametrize("change", [
    {"componentIssues": []}, {"componentIssues": [ISSUE] * 5},
    {"componentIssues": [{**ISSUE, "file": "../private.vue"}]},
    {"componentIssues": [{**ISSUE, "file": "src/Unknown.vue"}]},
    {"componentIssues": [{**ISSUE, "component": "<script>"}]},
    {"componentIssues": [{**ISSUE, "file": None}]},
    {"componentIssues": [{**ISSUE, "prop": 123}]},
    {"componentIssues": [{**ISSUE, "extra": "private"}]},
    {"built": True}, {"errorCode": "VUE_NPM_BUILD_FAILED"},
])
def test_invalid_or_unrelated_build_does_not_enable_workset(change):
    assert component_repair_issues({**BUILD, **change}, SNAPSHOT) == ([], [])


@pytest.mark.parametrize("change", [
    {"truncated": True}, {"omittedFileCount": 1}, {"includedFileCount": 1},
    {"eligibleFileCount": 3}, {"files": [{"path": "../App.vue"}]},
])
def test_incomplete_manifest_keeps_original_build_repair(change):
    assert component_repair_issues(BUILD, {**SNAPSHOT, **change}) == ([], [])


async def test_build_failure_enables_private_workset_without_leaking_issues(settings):
    class Gateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            return deepcopy(BUILD) if name == "project_build" else result

    gateway = Gateway(vue_source_snapshot=SNAPSHOT)
    emitter = EventEmitter("subgraph-test")
    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway)
    nodes = create_verification_nodes(workflow, emitter, "42:subgraph-test", {})
    update = await nodes["project_build"](initial())
    assert initial_repair_work(update["repair_issues"], update["repair_file_manifest"])
    assert "componentIssues" not in update["build"]
    assert [call["name"] for call in gateway.calls] == ["project_build", "vue_source_snapshot"]
    assert "componentIssues" not in str([event.model_dump() for event in emitter.events])
    assert "<template/>" not in str([event.model_dump() for event in emitter.events])


def test_read_batch_cannot_consume_last_tool_before_workset_write():
    issues, manifest = component_repair_issues(BUILD, SNAPSHOT)
    work = initial_repair_work(issues, manifest)
    work, _ = prepare_work_turn(work, asdict(ModelTurn("计划", repair_plan={"issueIndex": 0, "files": manifest})))
    with pytest.raises(ValueError, match="VUE_REPAIR_WORKSET_STALLED"):
        require_write_after_reads(work, [{"name": "file_read", "arguments": {"relativeFilePath": manifest[0]}}],
                                 remaining_tools=1)
    require_write_after_reads(work, [{"name": "file_modify", "arguments": {}}], remaining_tools=1)


def test_read_budget_reserve_only_blocks_prewrite_exhaustion():
    issues, manifest = component_repair_issues(BUILD, SNAPSHOT)
    work = initial_repair_work(issues, manifest)
    calls = [{"name": "file_read", "arguments": {"relativeFilePath": path}} for path in manifest]
    require_write_after_reads(work, calls, remaining_tools=3)
    with pytest.raises(ValueError, match="VUE_REPAIR_WORKSET_STALLED"):
        require_write_after_reads(work, calls, remaining_tools=2)
    require_write_after_reads({**work, "active_write": True}, calls, remaining_tools=2)


def test_workset_prompt_uses_explicit_completion_without_generic_empty_turn_advice():
    from ai_service.models.tool_contract import vue_tool_prompt

    prompt = vue_tool_prompt(repair_work=True)
    assert '"completedIssueIndex":0,"toolCalls":[]' in prompt
    assert "when all files are ready" not in prompt
    assert "completedIssueIndex" not in vue_tool_prompt()


async def test_legacy_build_response_does_not_read_extra_snapshot(settings):
    class Gateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            return {k: v for k, v in BUILD.items() if k != "componentIssues"} if name == "project_build" else result

    gateway = Gateway()
    nodes = create_verification_nodes(make_workflow(settings, FakeModel(), MemoryCheckpoint(), gateway),
                                      EventEmitter("subgraph-test"), "42:subgraph-test", {})
    update = await nodes["project_build"](initial())
    assert not update.get("repair_issues")
    assert [call["name"] for call in gateway.calls] == ["project_build"]


async def test_real_workflow_hard_failure_reaches_workset_and_rebuild(settings):
    class Gateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            result = await super().invoke(name, arguments, **kwargs)
            if name == "project_build" and sum(c["name"] == name for c in self.calls) == 1:
                return deepcopy(BUILD)
            return result

    model = FakeModel()
    gateway, checkpoint = Gateway(vue_source_snapshot=SNAPSHOT), MemoryCheckpoint()
    events = await make_workflow(settings, model, checkpoint, gateway).run(request_for("VUE_PROJECT"))
    assert events[-1].type == "completed"
    repairs = [context["context"] for name, context in model.calls if name == "repair"]
    assert repairs and repairs[0]["repairWork"]["issues"][0]["code"] == "TECHNICAL_ERROR"
    assert sum(call["name"] == "file_write" for call in gateway.calls) == 1
    assert sum(call["name"] == "project_build" for call in gateway.calls) == 2
    assert "componentIssues" not in str([event.model_dump() for event in events])


async def test_snapshot_cancellation_propagates(settings):
    import asyncio

    class Gateway(FakeToolGateway):
        async def invoke(self, name, arguments, **kwargs):
            if name == "vue_source_snapshot":
                raise asyncio.CancelledError
            return deepcopy(BUILD)

    workflow = make_workflow(settings, FakeModel(), MemoryCheckpoint(), Gateway())
    nodes = create_verification_nodes(workflow, EventEmitter("subgraph-test"), "42:subgraph-test", {})
    with pytest.raises(asyncio.CancelledError):
        await nodes["project_build"](initial())
