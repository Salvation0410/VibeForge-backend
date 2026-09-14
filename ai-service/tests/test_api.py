from fastapi.testclient import TestClient

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
        checkpoint = MemoryCheckpoint()
        client = TestClient(app_factory(model=model, checkpoint=checkpoint))
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
    completed = events[-1]
    assert completed["type"] == "completed"
    assert completed["data"]["repairCount"] == 2
    assert completed["data"]["qualityPassed"] is False


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
    vue_calls = [call for call in gateway.calls if call["name"] == "search_reference"]
    assert len(vue_calls) == 4
    assert all(call["toolCallId"].startswith("req-1:vue:") for call in vue_calls)
    assert len({call["toolCallId"] for call in vue_calls}) == 4
    assert len([event for event in events if event["type"] == "tool_started" and event["node"] == "vue_agent"]) == 4
    assert len([event for event in events if event["type"] == "tool_finished" and event["node"] == "vue_agent"]) == 4


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


def test_health_ready_reports_checkpoint_failure(app_factory):
    checkpoint = MemoryCheckpoint()
    checkpoint.available = False
    client = TestClient(app_factory(checkpoint=checkpoint))
    assert client.get("/internal/v1/health/live").json() == {"status": "live"}
    response = client.get("/internal/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
