import asyncio
import json
import logging

import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from ai_service.api.schemas import CodeGenType, GenerationRequest
from ai_service.infrastructure.spring_tools import SpringToolGateway
from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.workflow import GenerationWorkflow, _after_build
from conftest import FakeModel, FakeToolGateway, MemoryCheckpoint


def generation_payload(code_gen_type: str) -> dict:
    return {
        "requestId": "req-1",
        "appId": "42",
        "prompt": "build it",
        "codeGenType": code_gen_type,
        "conversation": [{"role": "user", "content": "previous detail"}],
    }


def test_authentication_is_required(app_factory, auth_headers):
    client = TestClient(app_factory())
    assert client.get("/internal/v1/health/live").status_code == 200
    assert client.post("/internal/v1/route", json={"prompt": "site"}).status_code == 401
    assert client.post(
        "/internal/v1/route",
        json={"prompt": "site"},
        headers={"Authorization": "Bearer wrong"},
    ).status_code == 401
    assert client.post(
        "/internal/v1/route", json={"prompt": "site"}, headers=auth_headers
    ).status_code == 200


def test_route_returns_supported_generation_type(app_factory, auth_headers):
    client = TestClient(app_factory(model=FakeModel()))
    response = client.post(
        "/internal/v1/route",
        json={"prompt": "Create a Vue dashboard", "appId": "42", "requestId": "route-1"},
        headers=auth_headers,
    )
    assert response.json() == {
        "requestId": "route-1",
        "codeGenType": "VUE_PROJECT",
    }


def test_all_generation_branches_complete_in_order(app_factory, auth_headers, ndjson_parser):
    for branch in ("HTML", "MULTI_FILE", "VUE_PROJECT"):
        model = FakeModel()
        gateway = FakeToolGateway()
        checkpoint = MemoryCheckpoint()
        client = TestClient(app_factory(model=model, gateway=gateway, checkpoint=checkpoint))
        response = client.post(
            "/internal/v1/generations:stream",
            json=generation_payload(branch),
            headers=auth_headers,
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/x-ndjson")
        events = ndjson_parser(response)
        assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
        assert events[-1]["type"] == "completed"
        completed = events[-1]["data"]
        assert "artifact" not in completed
        assert completed["threadId"] == "42:req-1"
        assert completed["codeGenType"] == branch
        assert completed["qualityPassed"] is True
        assert completed["repairCount"] == 0
        assert completed["toolCallCount"] >= 0
        if branch in {"HTML", "MULTI_FILE"}:
            assert completed["published"] is True
            assert completed["versionId"] == "req-1"
            assert completed["artifactHashes"] == {}
        else:
            assert completed["built"] is True
        assert all(event["requestId"] == "req-1" for event in events)
        generation_calls = [payload for name, payload in model.calls if name == "generate"]
        assert generation_calls[0]["branch"] == branch
        assert generation_calls[0]["context"]["currentArtifact"] == {"exists": False}
        assert "context_prepare" in [event["node"] for event in events]
        assert "quality_review" in [event["node"] for event in events]
        assert "42:req-1" in checkpoint.saved
        saved_states = checkpoint.saved["42:req-1"]
        assert saved_states
        assert all("artifact" not in state for state in saved_states)
        assert all("prompt" not in state for state in saved_states)
        assert all("conversation" not in state for state in saved_states)
        assert all("context" not in state for state in saved_states)
        assert checkpoint.cleaned_graph_threads == ["42:req-1"]
        build_calls = [call for call in gateway.calls if call["name"] == "project_build"]
        assert bool(build_calls) is (branch == "VUE_PROJECT")
        assert not [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]


def test_generation_failure_cleans_graph_checkpoint(app_factory, auth_headers, ndjson_parser):
    class FailingModel(FakeModel):
        async def generate(self, branch, context):
            raise RuntimeError("model generation failed")

    checkpoint = MemoryCheckpoint()
    client = TestClient(app_factory(model=FailingModel(), checkpoint=checkpoint))

    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert not [event for event in events if event["type"] == "completed"]
    assert checkpoint.cleaned_graph_threads == ["42:req-1"]


@pytest.mark.asyncio
async def test_cleanup_failure_does_not_override_completed_terminal_event(settings, caplog):
    class CleanupFailCheckpoint(MemoryCheckpoint):
        async def cleanup_graph(self, thread_id: str) -> None:
            raise RuntimeError("cleanup failed")

    workflow = GenerationWorkflow(
        model=FakeModel(),
        tool_gateway=FakeToolGateway(),
        checkpoint=CleanupFailCheckpoint(),
        cancellations=CancellationRegistry(),
        settings=settings,
    )
    request = GenerationRequest(
        requestId="req-cleanup-fail",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.HTML,
    )

    with caplog.at_level(logging.WARNING):
        events = await workflow.run(request)

    assert events[-1].type == "completed"
    assert not [event for event in events if event.type == "failed"]
    assert "42:req-cleanup-fail" in caplog.text


@pytest.mark.asyncio
async def test_task_cancellation_cleans_graph_checkpoint(settings):
    class BlockingModel(FakeModel):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def generate(self, branch, context):
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("阻塞模型不应正常返回")

    model = BlockingModel()
    checkpoint = MemoryCheckpoint()
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        settings=settings,
    )
    request = GenerationRequest(
        requestId="req-task-cancel",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.HTML,
    )
    task = asyncio.create_task(workflow.run(request))
    await asyncio.wait_for(model.started.wait(), timeout=1)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert checkpoint.cleaned_graph_threads == ["42:req-task-cancel"]


@pytest.mark.asyncio
async def test_stream_consumer_cancellation_waits_for_checkpoint_cleanup(settings):
    class BlockingModel(FakeModel):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()

        async def generate(self, branch, context):
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("阻塞模型不应正常返回")

    class DelayedCheckpoint(MemoryCheckpoint):
        async def cleanup_graph(self, thread_id: str) -> None:
            await asyncio.sleep(0)
            await super().cleanup_graph(thread_id)

    model = BlockingModel()
    checkpoint = DelayedCheckpoint()
    workflow = GenerationWorkflow(
        model=model,
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        settings=settings,
    )
    request = GenerationRequest(
        requestId="req-stream-cancel",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.HTML,
    )
    stream = workflow.stream(request)

    async def consume() -> None:
        async for _event in stream:
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(model.started.wait(), timeout=1)
    consumer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consumer
    await stream.aclose()

    assert checkpoint.cleaned_graph_threads == ["42:req-stream-cancel"]


def test_existing_artifact_context_is_loaded_before_generation(app_factory, auth_headers, ndjson_parser):
    current_artifact = {
        "exists": True,
        "codeGenType": "HTML",
        "entry": "index.html",
        "artifact": "```html\n<html><body>old</body></html>\n```",
    }
    model = FakeModel()
    gateway = FakeToolGateway(artifact_context=current_artifact)
    client = TestClient(app_factory(model=model, gateway=gateway))

    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "completed"
    context_calls = [call for call in gateway.calls if call["name"] == "artifact_context"]
    assert len(context_calls) == 1
    assert context_calls[0]["toolCallId"] == "req-1:artifact_context"
    assert context_calls[0]["arguments"] == {"codeGenType": "HTML"}
    generate_context = next(data for name, data in model.calls if name == "generate")["context"]
    assert generate_context["currentArtifact"] == current_artifact


def test_artifact_context_failure_stops_before_model_generation(app_factory, auth_headers, ndjson_parser):
    class FailingContextGateway(FakeToolGateway):
        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "artifact_context":
                raise RuntimeError("ARTIFACT_CONTEXT_READ_FAILED: failed to read active artifact")
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=FailingContextGateway())).post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "ARTIFACT_CONTEXT_READ_FAILED"
    assert not [call for call in model.calls if call[0] == "generate"]


@pytest.mark.parametrize("code_gen_type", ["HTML", "MULTI_FILE"])
def test_non_vue_repair_is_capped_without_source_snapshot(
    app_factory, auth_headers, ndjson_parser, code_gen_type
):
    model = FakeModel(reviews=[False, False, False, False])
    gateway = FakeToolGateway()
    client = TestClient(app_factory(model=model, gateway=gateway))
    response = client.post(
        "/internal/v1/generations:stream",
        json=generation_payload(code_gen_type),
        headers=auth_headers,
    )
    events = ndjson_parser(response)
    assert len([call for call in model.calls if call[0] == "repair"]) == 2
    assert events[-1]["type"] == "failed"
    assert not [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]


def test_vue_build_failure_is_repaired_and_rebuilt_before_review(
    app_factory, auth_headers, ndjson_parser
):
    class BuildSequenceGateway(FakeToolGateway):
        def __init__(self):
            super().__init__()
            self.build_results = [
                {"built": False, "errorCode": "VUE_NPM_BUILD_FAILED", "message": "vite failed"},
                {"built": True, "errorCode": "", "message": ""},
            ]

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "project_build":
                call = {
                    "name": name,
                    "arguments": arguments,
                    "appId": app_id,
                    "requestId": request_id,
                    "toolCallId": tool_call_id,
                }
                self.calls.append(call)
                return self.build_results.pop(0)
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    gateway = BuildSequenceGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert len([call for call in gateway.calls if call["name"] == "project_build"]) == 2
    repair_calls = [data for name, data in model.calls if name == "repair"]
    assert len(repair_calls) == 1
    assert repair_calls[0]["context"]["build"] == {
        "built": False,
        "errorCode": "VUE_NPM_BUILD_FAILED",
        "message": "vite failed",
    }
    assert len([call for call in model.calls if call[0] == "review"]) == 1
    snapshot_calls = [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0]["toolCallId"] == "req-1:vue-source-snapshot:1"
    assert snapshot_calls[0]["arguments"] == {"codeGenType": "VUE_PROJECT"}
    review = next(data for name, data in model.calls if name == "review")
    assert review["artifact"] == (
        '{"eligibleFileCount":1,"files":[{"content":"<template><main>fixed</main></template>",'
        '"path":"src/App.vue","truncated":false}],"includedFileCount":1,'
        '"omittedFileCount":0,"truncated":false}'
    )
    assert "vueSourceSnapshot" not in review["context"]
    finished = next(
        event for event in events
        if event["type"] == "tool_finished" and event["data"]["tool"] == "vue_source_snapshot"
    )
    assert finished["data"]["result"] == {
        "eligibleFileCount": 1,
        "includedFileCount": 1,
        "omittedFileCount": 0,
        "truncated": False,
    }
    assert "fixed" not in json.dumps(finished, ensure_ascii=False)
    assert events[-1]["type"] == "completed"


def test_vue_snapshot_failure_stops_before_review_without_fallback(
    app_factory, auth_headers, ndjson_parser
):
    class SnapshotFailureGateway(FakeToolGateway):
        def __init__(self):
            super().__init__()
            self.build_count = 0

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "project_build":
                self.build_count += 1
                if self.build_count == 1:
                    return {"built": False, "errorCode": "VUE_NPM_BUILD_FAILED", "message": "broken"}
            if name == "vue_source_snapshot":
                raise RuntimeError("VUE_SOURCE_SNAPSHOT_FAILED: snapshot unavailable")
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=SnapshotFailureGateway())).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "VUE_SOURCE_SNAPSHOT_FAILED"
    assert not [call for call in model.calls if call[0] == "review"]
    assert not [event for event in events if event["type"] == "completed"]


def test_truncated_vue_snapshot_is_reviewed_after_repair(
    app_factory, auth_headers, ndjson_parser
):
    snapshot = {
        "files": [{"path": "src/App.vue", "content": "fixed partial", "truncated": True}],
        "eligibleFileCount": 2,
        "includedFileCount": 1,
        "omittedFileCount": 1,
        "truncated": True,
    }

    class RepairingGateway(FakeToolGateway):
        def __init__(self):
            super().__init__(vue_source_snapshot=snapshot)
            self.build_count = 0

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "project_build":
                self.build_count += 1
                if self.build_count == 1:
                    return {"built": False, "errorCode": "VUE_NPM_BUILD_FAILED", "message": "broken"}
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=RepairingGateway())).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    review = next(data for name, data in model.calls if name == "review")
    assert json.loads(review["artifact"])["truncated"] is True
    assert json.loads(review["artifact"])["files"][0]["truncated"] is True
    assert events[-1]["type"] == "completed"


def test_failed_repaired_vue_snapshot_review_triggers_second_repair(
    app_factory, auth_headers, ndjson_parser
):
    snapshots = [
        {
            "files": [{"path": "src/App.vue", "content": "snapshot-v1", "truncated": False}],
            "eligibleFileCount": 1,
            "includedFileCount": 1,
            "omittedFileCount": 0,
            "truncated": False,
        },
        {
            "files": [{"path": "src/App.vue", "content": "snapshot-v2", "truncated": False}],
            "eligibleFileCount": 1,
            "includedFileCount": 1,
            "omittedFileCount": 0,
            "truncated": False,
        },
    ]

    class SequencedSnapshotGateway(FakeToolGateway):
        def __init__(self):
            super().__init__()
            self.snapshots = list(snapshots)

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            result = await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )
            if name == "vue_source_snapshot":
                return self.snapshots.pop(0)
            return result

    model = FakeModel(reviews=[False, False, True])
    gateway = SequencedSnapshotGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert len([call for call in model.calls if call[0] == "repair"]) == 2
    snapshot_calls = [call for call in gateway.calls if call["name"] == "vue_source_snapshot"]
    assert [call["toolCallId"] for call in snapshot_calls] == [
        "req-1:vue-source-snapshot:1",
        "req-1:vue-source-snapshot:2",
    ]
    review_calls = [data for name, data in model.calls if name == "review"]
    assert review_calls[0]["artifact"] == "artifact:VUE_PROJECT"
    assert "snapshot-v1" in review_calls[1]["artifact"]
    assert "snapshot-v2" not in review_calls[1]["artifact"]
    assert "snapshot-v2" in review_calls[2]["artifact"]
    assert "snapshot-v1" not in review_calls[2]["artifact"]
    assert events[-1]["type"] == "completed"
    assert events[-1]["data"]["repairCount"] == 2


def test_vue_snapshot_review_failure_does_not_leak_source_to_events(
    app_factory, auth_headers, ndjson_parser
):
    secret = "TOP_SECRET_SOURCE"
    snapshot = {
        "files": [{"path": "src/App.vue", "content": secret, "truncated": False}],
        "eligibleFileCount": 1,
        "includedFileCount": 1,
        "omittedFileCount": 0,
        "truncated": False,
    }

    class LeakingReviewModel(FakeModel):
        async def review(self, artifact, context):
            self.calls.append(("review", {"artifact": artifact, "context": context}))
            if artifact == "artifact:VUE_PROJECT":
                return False
            raise RuntimeError(f"provider rejected artifact: {artifact}")

    model = LeakingReviewModel()
    events = ndjson_parser(TestClient(app_factory(
        model=model,
        gateway=FakeToolGateway(vue_source_snapshot=snapshot),
    )).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["message"] == "Vue source snapshot review failed"
    serialized_events = json.dumps(events, ensure_ascii=False)
    assert secret not in serialized_events
    assert "src/App.vue" not in serialized_events
    assert len([call for call in model.calls if call[0] == "review"]) == 2


def test_vue_build_failure_exhausts_two_repairs_without_review_or_completion(
    app_factory, auth_headers, ndjson_parser
):
    class AlwaysFailingBuildGateway(FakeToolGateway):
        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name == "project_build":
                call = {
                    "name": name,
                    "arguments": arguments,
                    "appId": app_id,
                    "requestId": request_id,
                    "toolCallId": tool_call_id,
                }
                self.calls.append(call)
                return {
                    "built": False,
                    "errorCode": "VUE_NPM_BUILD_FAILED",
                    "message": "vite failed repeatedly",
                }
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    gateway = AlwaysFailingBuildGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert len([call for call in gateway.calls if call["name"] == "project_build"]) == 3
    assert len([call for call in model.calls if call[0] == "repair"]) == 2
    assert not [call for call in model.calls if call[0] == "review"]
    assert events[-1]["type"] == "failed"
    assert not [event for event in events if event["type"] == "completed"]


def test_after_build_requires_literal_boolean_true():
    assert _after_build(
        {"build": {"built": "false"}, "repair_count": 0},
        max_attempts=2,
    ) == "repair"


def test_validation_failure_after_build_repair_does_not_reuse_old_build_error(
    app_factory, auth_headers, ndjson_parser
):
    class BuildThenValidationFailureGateway(FakeToolGateway):
        def __init__(self):
            super().__init__()
            self.validation_results = [
                {"valid": True, "errors": []},
                {"valid": False, "errors": [{"code": "VALIDATION_AFTER_BUILD_REPAIR"}]},
                {"valid": False, "errors": [{"code": "VALIDATION_AFTER_BUILD_REPAIR"}]},
            ]

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            if name in {"artifact_validate", "project_build"}:
                self.calls.append({
                    "name": name,
                    "arguments": arguments,
                    "appId": app_id,
                    "requestId": request_id,
                    "toolCallId": tool_call_id,
                })
                if name == "artifact_validate":
                    return self.validation_results.pop(0)
                return {
                    "built": False,
                    "errorCode": "VUE_NPM_BUILD_FAILED",
                    "message": "old build failure",
                }
            return await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )

    model = FakeModel()
    gateway = BuildThenValidationFailureGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert len([call for call in model.calls if call[0] == "repair"]) == 2
    assert not [call for call in model.calls if call[0] == "review"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] != "VUE_NPM_BUILD_FAILED"
    assert "VALIDATION_AFTER_BUILD_REPAIR" in events[-1]["error"]["message"]
    assert not [event for event in events if event["type"] == "completed"]


def test_truncated_multi_file_response_fails_before_publication(app_factory, auth_headers, ndjson_parser):
    class TruncatedModel(FakeModel):
        async def generate(self, branch: str, context: dict) -> ModelTurn:
            return ModelTurn(content="partial", finish_reason="LENGTH")

    gateway = FakeToolGateway()
    client = TestClient(app_factory(model=TruncatedModel(), gateway=gateway))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("MULTI_FILE"),
        headers=auth_headers,
    ))
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "MODEL_OUTPUT_TRUNCATED"
    assert "MODEL_OUTPUT_TRUNCATED" in events[-1]["error"]["message"]
    assert not [call for call in gateway.calls if call["name"] == "artifact_publish"]


def test_multi_file_completes_only_after_publication(app_factory, auth_headers, ndjson_parser):
    gateway = FakeToolGateway()
    client = TestClient(app_factory(gateway=gateway))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("MULTI_FILE"),
        headers=auth_headers,
    ))
    assert events[-1]["type"] == "completed"
    calls = [call["name"] for call in gateway.calls]
    assert "artifact_publish" in calls
    assert "project_build" not in calls


def test_html_completes_only_after_successful_publication(app_factory, auth_headers, ndjson_parser):
    """HTML 必须在 Spring 确认发布后才产生 completed 终态。"""
    gateway = FakeToolGateway()
    client = TestClient(app_factory(gateway=gateway))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))

    publish_calls = [call for call in gateway.calls if call["name"] == "artifact_publish"]
    assert len(publish_calls) == 1
    assert publish_calls[0]["arguments"]["codeGenType"] == "HTML"
    publish_finished = next(i for i, event in enumerate(events)
                            if event["type"] == "tool_finished" and event["node"] == "artifact_publish")
    completed = next(i for i, event in enumerate(events) if event["type"] == "completed")
    assert publish_finished < completed


def test_html_publication_rejection_fails_without_completed(app_factory, auth_headers, ndjson_parser):
    """Spring 拒绝 HTML 发布时工作流只能发送 failed，不得伪造成功。"""
    class RejectingGateway(FakeToolGateway):
        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            result = await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )
            return {"published": False} if name == "artifact_publish" else result

    gateway = RejectingGateway()
    events = ndjson_parser(TestClient(app_factory(gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"] == {
        "code": "GENERATION_FAILED",
        "message": "Spring rejected artifact publication",
    }
    assert len([call for call in gateway.calls if call["name"] == "artifact_publish"]) == 1
    assert not [event for event in events if event["type"] == "completed"]


def test_spring_business_error_code_reaches_failed_event(app_factory, auth_headers, ndjson_parser):
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read())
        requests.append(body)
        if body["toolName"] == "artifact_context":
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"exists": False, "codeGenType": "HTML"},
                    "message": "ok",
                },
            )
        if body["toolName"] == "artifact_validate":
            return httpx.Response(
                200,
                json={"code": 0, "data": {"valid": True, "errors": []}, "message": "ok"},
            )
        assert body["toolName"] == "artifact_publish"
        return httpx.Response(
            200,
            json={
                "code": 50001,
                "data": None,
                "message": "TOOL_EXECUTION_INDETERMINATE",
            },
        )

    gateway = SpringToolGateway(
        base_url="http://spring.test/api/internal/ai-tools",
        bearer_token="gateway-token",
        transport=httpx.MockTransport(handler),
    )
    with TestClient(app_factory(gateway=gateway)) as client:
        events = ndjson_parser(client.post(
            "/internal/v1/generations:stream",
            json=generation_payload("HTML"),
            headers=auth_headers,
        ))

    assert events[-1]["type"] == "failed"
    assert events[-1]["error"] == {
        "code": "TOOL_EXECUTION_INDETERMINATE",
        "message": "TOOL_EXECUTION_INDETERMINATE: Spring tool request failed (code=50001)",
    }
    assert len([request for request in requests if request["toolName"] == "artifact_publish"]) == 1
    assert not [event for event in events if event["type"] == "completed"]


def test_html_remains_completed_when_graph_checkpoint_fails_after_publication(
    app_factory, auth_headers, ndjson_parser
):
    """HTML 已提交后即使图 checkpoint 失败，也只补发一次 completed。"""
    gateway = FakeToolGateway()

    class FailAfterPublishSaver(InMemorySaver):
        async def aput(self, config, checkpoint, metadata, new_versions):
            if any(call["name"] == "artifact_publish" for call in gateway.calls):
                raise RuntimeError("checkpoint failed after html publication")
            return await super().aput(config, checkpoint, metadata, new_versions)

    class GraphCheckpoint(MemoryCheckpoint):
        def __init__(self):
            super().__init__()
            self.graph_saver = FailAfterPublishSaver()

        def get_graph_saver(self):
            return self.graph_saver

    events = ndjson_parser(TestClient(app_factory(gateway=gateway, checkpoint=GraphCheckpoint())).post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))
    completed_events = [event for event in events if event["type"] == "completed"]
    assert len(completed_events) == 1
    assert "artifact" not in completed_events[0]["data"]
    assert completed_events[0]["data"]["published"] is True
    assert completed_events[0]["data"]["versionId"] == "req-1"
    assert completed_events[0]["data"]["artifactHashes"] == {}
    assert not [event for event in events if event["type"] == "failed"]


def test_multi_file_remains_completed_when_graph_checkpoint_fails_after_publication(
    app_factory, auth_headers, ndjson_parser
):
    gateway = FakeToolGateway()

    class FailAfterPublishSaver(InMemorySaver):
        """模拟 Spring 已发布后 LangGraph 自动 checkpoint 写入失败。"""

        async def aput(self, config, checkpoint, metadata, new_versions):
            if any(call["name"] == "artifact_publish" for call in gateway.calls):
                raise RuntimeError("checkpoint failed after artifact publication")
            return await super().aput(config, checkpoint, metadata, new_versions)

    class GraphCheckpoint(MemoryCheckpoint):
        """同时提供业务 checkpoint 与故障注入用 LangGraph saver。"""

        def __init__(self):
            super().__init__()
            self.graph_saver = FailAfterPublishSaver()

        def get_graph_saver(self):
            return self.graph_saver

    client = TestClient(app_factory(gateway=gateway, checkpoint=GraphCheckpoint()))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("MULTI_FILE"),
        headers=auth_headers,
    ))

    completed_events = [event for event in events if event["type"] == "completed"]
    assert len(completed_events) == 1
    assert "artifact" not in completed_events[0]["data"]
    assert completed_events[0]["data"]["published"] is True
    assert completed_events[0]["data"]["versionId"] == "req-1"
    assert completed_events[0]["data"]["artifactHashes"] == {}
    assert not [event for event in events if event["type"] == "failed"]
    assert any(call["name"] == "artifact_publish" for call in gateway.calls)


def test_multi_file_records_publication_before_tool_finished_event(
    app_factory, auth_headers, ndjson_parser, monkeypatch
):
    gateway = FakeToolGateway()
    original_emit = EventEmitter.emit
    failed_once = False

    async def fail_publish_finished_once(self, event_type, node, *, data=None, error=None):
        nonlocal failed_once
        if (
            not failed_once
            and event_type == "tool_finished"
            and node == "artifact_publish"
        ):
            failed_once = True
            raise RuntimeError("tool_finished delivery failed")
        return await original_emit(self, event_type, node, data=data, error=error)

    monkeypatch.setattr(EventEmitter, "emit", fail_publish_finished_once)
    client = TestClient(app_factory(gateway=gateway))
    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("MULTI_FILE"),
        headers=auth_headers,
    ))

    completed_events = [event for event in events if event["type"] == "completed"]
    assert len(completed_events) == 1
    assert "artifact" not in completed_events[0]["data"]
    assert completed_events[0]["data"]["published"] is True
    assert completed_events[0]["data"]["versionId"] == "req-1"
    assert completed_events[0]["data"]["artifactHashes"] == {}
    assert not [event for event in events if event["type"] == "failed"]
    assert any(call["name"] == "artifact_publish" for call in gateway.calls)


def test_vue_agent_tool_calls_are_bounded_and_identified(app_factory, auth_headers, ndjson_parser):
    model = FakeModel(vue_tool_calls=10)
    gateway = FakeToolGateway()
    client = TestClient(app_factory(model=model, gateway=gateway))
    events = ndjson_parser(
        client.post(
            "/internal/v1/generations:stream",
            json=generation_payload("VUE_PROJECT"),
            headers=auth_headers,
        )
    )
    vue_calls = [call for call in gateway.calls if call["name"] == "file_read"]
    assert len(vue_calls) == 4
    assert all(call["toolCallId"].startswith("req-1:vue-generate:") for call in vue_calls)
    assert len({call["toolCallId"] for call in vue_calls}) == 4
    assert all(call["appId"] == "42" for call in vue_calls)
    assert all(call["requestId"] == "req-1" for call in vue_calls)
    assert all("appId" not in call["arguments"] for call in vue_calls)
    assert all(call["arguments"]["codeGenType"] == "VUE_PROJECT" for call in vue_calls)
    assert len([event for event in events if event["type"] == "tool_started" and event["node"] == "vue_agent"]) == 4
    assert len([event for event in events if event["type"] == "tool_finished" and event["node"] == "vue_agent"]) == 4


@pytest.mark.parametrize(
    ("finish_reason", "error_code"),
    [
        ("LENGTH", "MODEL_OUTPUT_TRUNCATED"),
        ("CONTENT_FILTER", "MODEL_OUTPUT_BLOCKED"),
        ("CONTENT_FILTERED", "MODEL_OUTPUT_BLOCKED"),
    ],
)
def test_incomplete_first_vue_turn_fails_before_executing_model_tool(
    app_factory, auth_headers, ndjson_parser, finish_reason, error_code
):
    class IncompleteVueModel(FakeModel):
        async def generate(self, branch, context):
            return ModelTurn(
                content="partial",
                tool_calls=[ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"})],
                finish_reason=finish_reason,
            )

    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=IncompleteVueModel(), gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert not [call for call in gateway.calls if call["name"] == "file_read"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == error_code


@pytest.mark.parametrize(
    ("finish_reason", "error_code"),
    [
        ("LENGTH", "MODEL_OUTPUT_TRUNCATED"),
        ("CONTENT_FILTER", "MODEL_OUTPUT_BLOCKED"),
    ],
)
def test_incomplete_later_vue_turn_emits_no_content_or_tool_call(
    app_factory, auth_headers, ndjson_parser, finish_reason, error_code
):
    class IncompleteSecondTurnModel(FakeModel):
        async def generate(self, branch, context):
            if not context.get("toolResults"):
                return ModelTurn(
                    content="first turn",
                    tool_calls=[ToolCall(
                        name="file_read",
                        arguments={"relativeFilePath": "src/App.vue"},
                    )],
                )
            return ModelTurn(
                content="must-not-be-emitted",
                tool_calls=[ToolCall(
                    name="file_modify",
                    arguments={
                        "relativeFilePath": "src/App.vue",
                        "oldContent": "broken",
                        "newContent": "fixed",
                    },
                )],
                finish_reason=finish_reason,
            )

    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(
        model=IncompleteSecondTurnModel(),
        gateway=gateway,
    )).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    vue_calls = [call for call in gateway.calls if ":vue-generate:" in call["toolCallId"]]
    assert [call["name"] for call in vue_calls] == ["file_read"]
    emitted_content = [
        event["data"]["content"]
        for event in events
        if event["type"] == "content_delta" and event["node"] == "vue_agent"
    ]
    assert emitted_content == ["first turn"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == error_code


@pytest.mark.asyncio
async def test_vue_tool_loop_sums_token_usage_across_model_turns(app_factory):
    class TokenUsageModel(FakeModel):
        async def generate(self, branch, context):
            self.calls.append(("generate", {"branch": branch, "context": context}))
            if len(context.get("toolResults", [])) == 0:
                return ModelTurn(
                    content="inspect",
                    tool_calls=[ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"})],
                    token_usage={"input_tokens": 2, "output_tokens": 3},
                )
            return ModelTurn(
                content="done",
                finish_reason="STOP",
                token_usage={"input_tokens": 5, "output_tokens": 7},
            )

    app = app_factory(model=TokenUsageModel(), gateway=FakeToolGateway())
    result = await app.state.workflow._run_vue_tool_loop(
        state={
            "app_id": "42",
            "request_id": "req-1",
            "code_gen_type": "VUE_PROJECT",
            "context": {},
            "tool_call_count": 0,
        },
        emitter=EventEmitter("req-1"),
        thread_id="42:req-1",
        node="vue_agent",
        call_id_prefix="req-1:vue-generate",
        invoke_model=lambda context: app.state.model.generate("VUE_PROJECT", context),
    )

    assert result["token_usage"] == {"input_tokens": 7, "output_tokens": 10}


def test_vue_repair_executes_file_tools_through_spring(app_factory, auth_headers, ndjson_parser):
    class VueRepairModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False, True])
            self.repair_turn = 0

        async def repair(self, artifact, context):
            self.calls.append(("repair", {"artifact": artifact, "context": context}))
            self.repair_turn += 1
            if self.repair_turn == 1:
                return ModelTurn(
                    content="read current component",
                    tool_calls=[ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"})],
                )
            if self.repair_turn == 2:
                return ModelTurn(
                    content="apply targeted fix",
                    tool_calls=[ToolCall(
                        name="file_modify",
                        arguments={
                            "relativeFilePath": "src/App.vue",
                            "oldContent": "broken",
                            "newContent": "fixed",
                        },
                    )],
                )
            return ModelTurn(content="repair completed", finish_reason="STOP")

    model = VueRepairModel()
    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    repair_calls = [
        call for call in gateway.calls
        if call["toolCallId"].startswith("req-1:vue-repair:1:")
    ]
    assert [call["name"] for call in repair_calls] == ["file_read", "file_modify"]
    assert all(call["arguments"]["codeGenType"] == "VUE_PROJECT" for call in repair_calls)
    repair_model_calls = [data for name, data in model.calls if name == "repair"]
    assert repair_model_calls[1]["context"]["toolResults"][0]["tool"] == "file_read"
    assert repair_model_calls[2]["context"]["toolResults"][1]["tool"] == "file_modify"
    assert len([call for call in gateway.calls if call["name"] == "artifact_validate"]) == 2
    assert len([call for call in gateway.calls if call["name"] == "project_build"]) == 2
    assert events[-1]["type"] == "completed"
    assert "artifact" not in events[-1]["data"]
    assert events[-1]["data"]["built"] is True
    assert events[-1]["data"]["toolCallCount"] == 2


def test_invalid_vue_repair_tool_is_rejected_before_spring_gateway(
    app_factory, auth_headers, ndjson_parser
):
    class InvalidRepairModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False])

        async def repair(self, artifact, context):
            return ModelTurn(
                content="",
                tool_calls=[ToolCall(name="search_reference", arguments={"q": "layout"})],
            )

    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=InvalidRepairModel(), gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert not [call for call in gateway.calls if call["name"] == "search_reference"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "INVALID_VUE_TOOL_CALL"


@pytest.mark.parametrize(
    ("controlled_name", "controlled_value"),
    [("appId", "other-app"), ("codeGenType", "HTML")],
)
def test_vue_repair_rejects_model_controlled_arguments_before_spring(
    app_factory, auth_headers, ndjson_parser, controlled_name, controlled_value
):
    class ControlledArgumentModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False])

        async def repair(self, artifact, context):
            return ModelTurn(
                content="",
                tool_calls=[ToolCall(
                    name="file_read",
                    arguments={
                        "relativeFilePath": "src/App.vue",
                        controlled_name: controlled_value,
                    },
                )],
            )

    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=ControlledArgumentModel(), gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    repair_calls = [
        call for call in gateway.calls
        if call["toolCallId"].startswith("req-1:vue-repair:")
    ]
    assert repair_calls == []
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "INVALID_VUE_TOOL_CALL"
    assert controlled_name in events[-1]["error"]["message"]


def test_vue_generation_and_repair_share_total_tool_budget(
    app_factory, auth_headers, ndjson_parser
):
    class BudgetedRepairModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False, True], vue_tool_calls=3)

        async def repair(self, artifact, context):
            self.calls.append(("repair", {"artifact": artifact, "context": context}))
            if len(context.get("toolResults", [])) == 4:
                return ModelTurn(content="repair completed", finish_reason="STOP")
            return ModelTurn(
                content="continue repair",
                tool_calls=[ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"})],
            )

    model = BudgetedRepairModel()
    gateway = FakeToolGateway()
    events = ndjson_parser(TestClient(app_factory(model=model, gateway=gateway)).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    vue_file_calls = [call for call in gateway.calls if call["name"] == "file_read"]
    assert len(vue_file_calls) == 4
    assert len([call for call in vue_file_calls if ":vue-generate:" in call["toolCallId"]]) == 3
    assert len([call for call in vue_file_calls if ":vue-repair:1:" in call["toolCallId"]]) == 1
    repair_model_calls = [data for name, data in model.calls if name == "repair"]
    assert len(repair_model_calls) == 2
    assert len(repair_model_calls[1]["context"]["toolResults"]) == 4
    assert repair_model_calls[1]["context"]["toolResults"][-1]["toolCallId"] == (
        "req-1:vue-repair:1:1"
    )
    assert events[-1]["type"] == "completed"


def test_vue_repair_checks_cancellation_before_each_tool_call(
    app_factory, auth_headers, ndjson_parser
):
    class TwoToolRepairModel(FakeModel):
        def __init__(self):
            super().__init__(reviews=[False])

        async def repair(self, artifact, context):
            return ModelTurn(
                content="",
                tool_calls=[
                    ToolCall(name="file_read", arguments={"relativeFilePath": "src/App.vue"}),
                    ToolCall(
                        name="file_modify",
                        arguments={
                            "relativeFilePath": "src/App.vue",
                            "oldContent": "broken",
                            "newContent": "fixed",
                        },
                    ),
                ],
            )

    class CancellingGateway(FakeToolGateway):
        cancellations = None

        async def invoke(self, name, arguments, *, app_id, request_id, tool_call_id):
            result = await super().invoke(
                name,
                arguments,
                app_id=app_id,
                request_id=request_id,
                tool_call_id=tool_call_id,
            )
            if tool_call_id.startswith("req-1:vue-repair:1:1"):
                self.cancellations.cancel("42:req-1")
            return result

    gateway = CancellingGateway()
    app = app_factory(model=TwoToolRepairModel(), gateway=gateway)
    gateway.cancellations = app.state.cancellations
    events = ndjson_parser(TestClient(app).post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    repair_calls = [
        call for call in gateway.calls
        if call["toolCallId"].startswith("req-1:vue-repair:1:")
    ]
    assert [call["name"] for call in repair_calls] == ["file_read"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "cancelled"


def test_invalid_vue_tool_is_rejected_before_spring_gateway(app_factory, auth_headers, ndjson_parser):
    class InvalidToolModel(FakeModel):
        async def generate(self, branch, context):
            if branch == "VUE_PROJECT":
                return ModelTurn(tool_calls=[ToolCall(name="search_reference", arguments={"q": "layout"})], content="")
            return await super().generate(branch, context)

    gateway = FakeToolGateway()
    client = TestClient(app_factory(model=InvalidToolModel(), gateway=gateway))

    events = ndjson_parser(client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("VUE_PROJECT"),
        headers=auth_headers,
    ))

    assert [call["name"] for call in gateway.calls] == ["artifact_context"]
    assert events[-1]["type"] == "failed"
    assert events[-1]["error"]["code"] == "INVALID_VUE_TOOL_CALL"
    assert "search_reference" in events[-1]["error"]["message"]


def test_cancel_endpoint_marks_generation_cancelled(app_factory, auth_headers):
    app = app_factory()
    client = TestClient(app)
    response = client.post(
        "/internal/v1/generations/req-cancel:cancel",
        json={"appId": "42"},
        headers=auth_headers,
    )
    assert response.status_code == 202
    assert response.json() == {"requestId": "req-cancel", "status": "cancelled"}
    assert app.state.cancellations.is_cancelled("42:req-cancel")


def test_cancelled_generation_has_explicit_terminal_event(app_factory, auth_headers, ndjson_parser):
    checkpoint = MemoryCheckpoint()
    app = app_factory(checkpoint=checkpoint)
    client = TestClient(app)
    client.post(
        "/internal/v1/generations/req-cancel:cancel",
        json={"appId": "42"},
        headers=auth_headers,
    )
    payload = generation_payload("HTML")
    payload["requestId"] = "req-cancel"
    events = ndjson_parser(
        client.post("/internal/v1/generations:stream", json=payload, headers=auth_headers)
    )
    assert events[-1]["type"] == "failed"
    assert events[-1]["data"]["status"] == "cancelled"
    assert events[-1]["error"]["code"] == "cancelled"
    assert not app.state.cancellations.is_cancelled("42:req-cancel")
    assert checkpoint.cleaned_graph_threads == ["42:req-cancel"]


def test_health_ready_reports_checkpoint_failure(app_factory):
    checkpoint = MemoryCheckpoint()
    checkpoint.available = False
    client = TestClient(app_factory(checkpoint=checkpoint))
    assert client.get("/internal/v1/health/live").json() == {"status": "live"}
    response = client.get("/internal/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
