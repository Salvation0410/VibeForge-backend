from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from ai_service.models.base import ModelTurn, ToolCall
from ai_service.orchestration.events import EventEmitter
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
        assert all(event["requestId"] == "req-1" for event in events)
        generation_calls = [payload for name, payload in model.calls if name == "generate"]
        assert generation_calls[0]["branch"] == branch
        assert "context_prepare" in [event["node"] for event in events]
        assert "quality_review" in [event["node"] for event in events]
        assert "42:req-1" in checkpoint.saved
        build_calls = [call for call in gateway.calls if call["name"] == "project_build"]
        assert bool(build_calls) is (branch == "VUE_PROJECT")


def test_repair_is_capped_at_two_attempts(app_factory, auth_headers, ndjson_parser):
    model = FakeModel(reviews=[False, False, False, False])
    client = TestClient(app_factory(model=model))
    response = client.post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    )
    events = ndjson_parser(response)
    assert len([call for call in model.calls if call[0] == "repair"]) == 2
    assert events[-1]["type"] == "failed"
    assert len([call for call in model.calls if call[0] == "repair"]) == 2


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
        async def invoke(self, name, arguments, *, tool_call_id):
            result = await super().invoke(name, arguments, tool_call_id=tool_call_id)
            return {"published": False} if name == "artifact_publish" else result

    events = ndjson_parser(TestClient(app_factory(gateway=RejectingGateway())).post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))
    assert events[-1]["type"] == "failed"
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
    assert len([event for event in events if event["type"] == "completed"]) == 1
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

    assert len([event for event in events if event["type"] == "completed"]) == 1
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

    assert len([event for event in events if event["type"] == "completed"]) == 1
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
    assert all(call["toolCallId"].startswith("req-1:vue:") for call in vue_calls)
    assert len({call["toolCallId"] for call in vue_calls}) == 4
    assert all(call["appId"] == "42" for call in vue_calls)
    assert all(call["requestId"] == "req-1" for call in vue_calls)
    assert all("appId" not in call["arguments"] for call in vue_calls)
    assert all(call["arguments"]["codeGenType"] == "VUE_PROJECT" for call in vue_calls)
    assert len([event for event in events if event["type"] == "tool_started" and event["node"] == "vue_agent"]) == 4
    assert len([event for event in events if event["type"] == "tool_finished" and event["node"] == "vue_agent"]) == 4


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

    assert not gateway.calls
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
    app = app_factory()
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


def test_health_ready_reports_checkpoint_failure(app_factory):
    checkpoint = MemoryCheckpoint()
    checkpoint.available = False
    client = TestClient(app_factory(checkpoint=checkpoint))
    assert client.get("/internal/v1/health/live").json() == {"status": "live"}
    response = client.get("/internal/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
