"""真实 ASGI 路由加合成模型/存储，不连接网络或供应商模型。"""

import asyncio
import json

import pytest
from starlette.requests import ClientDisconnect

from conftest import FakeModel
from test_stream_terminal_cleanup import CleanupGate, ExitingModel, advance_ready_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("spec", ["2.3", "2.4"])
@pytest.mark.parametrize("phase", ["model", "cleanup"])
async def test_asgi_disconnect_waits_for_generation_cleanup(app_factory, spec, phase):
    checkpoint = CleanupGate()
    model = ExitingModel() if phase == "model" else FakeModel()
    app = app_factory(model=model, checkpoint=checkpoint)
    incoming = asyncio.Queue()
    disconnect = asyncio.Event()
    sent = []
    request_id = "asgi-disconnect"
    thread_id = f"42:{request_id}"
    await incoming.put({"type": "http.request", "body": json.dumps({
        "appId": "42", "requestId": request_id, "prompt": "build", "codeGenType": "HTML",
    }).encode(), "more_body": False})

    async def receive():
        return await incoming.get()

    async def send(message):
        sent.append(message)
        if spec == "2.4" and message["type"] == "http.response.body":
            # ASGI 2.4 通过发送失败报告断连；阻塞发送时图仍独立运行。
            await disconnect.wait()
            raise OSError("TEST_PEER_DISCONNECTED")

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": spec},
        "method": "POST", "scheme": "http", "path": "/internal/v1/generations:stream",
        "raw_path": b"/internal/v1/generations:stream", "query_string": b"",
        "root_path": "", "http_version": "1.1", "server": ("test", 80), "client": ("test", 1234),
        "headers": [(b"authorization", b"Bearer test-secret"), (b"content-type", b"application/json")],
    }
    response = asyncio.create_task(app(scope, receive, send))
    try:
        gate = model.started if phase == "model" else checkpoint.entered
        await asyncio.wait_for(gate.wait(), 3)
        if spec == "2.3":
            await incoming.put({"type": "http.disconnect"})
        else:
            disconnect.set()
        if phase == "model":
            await asyncio.wait_for(model.exiting.wait(), 3)
            assert not response.done()
            model.release.set()
        await asyncio.wait_for(checkpoint.entered.wait(), 3)
        await advance_ready_tasks()
        assert not response.done()
        assert not checkpoint.interrupted.is_set()
        checkpoint.release.set()
        if spec == "2.4":
            with pytest.raises(ClientDisconnect):
                await asyncio.wait_for(response, 3)
        else:
            await asyncio.wait_for(response, 3)
        assert checkpoint.cleaned_graph_threads == [thread_id]
        assert not app.state.active_generations.is_active(thread_id)
        assert not app.state.cancellations.is_cancelled(thread_id)
        assert sent[0]["status"] == 200
    finally:
        disconnect.set()
        checkpoint.release.set()
        if phase == "model":
            model.release.set()
        if not response.done():
            response.cancel()
        await asyncio.gather(response, return_exceptions=True)
