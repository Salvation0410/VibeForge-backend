"""独立验收代理测试：所有上游都使用 MockTransport，不访问真实供应商。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import traceback

import httpx
import pytest
from fastapi.testclient import TestClient


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "acceptance_model_proxy.py"
SPEC = importlib.util.spec_from_file_location("acceptance_model_proxy", SCRIPT)
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)
AUTH = {"Authorization": "Bearer local-secret"}


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(b'data: {"text":"hello"}\n\n', b"data: [DONE]\n\n"), pause=False):
        self.chunks = chunks
        self.pause = pause
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
            if self.pause:
                await asyncio.Event().wait()

    async def aclose(self):
        self.closed = True


def setup_proxy(tmp_path, handler, max_calls=3):
    ledger = proxy.BudgetLedger(tmp_path / "budget.json", max_calls)
    app = proxy.create_app(ledger=ledger, upstream_base_url="https://upstream.example/v1",
        upstream_api_key="upstream-secret", local_token="local-secret", transport=httpx.MockTransport(handler))
    return ledger, app


def test_dry_run_is_standalone_and_creates_nothing(tmp_path):
    # -I 隔离 Python 路径，证明 dry-run 不需要项目包，也不会加载 Settings。
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), "--ledger", str(tmp_path / "absent.json")],
        cwd=tmp_path, text=True, capture_output=True, check=True)
    assert "DRY RUN" in result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("amount", [0, 61, -1])
def test_invalid_budget_rejected_before_creating_ledger(tmp_path, amount):
    with pytest.raises(ValueError):
        proxy.BudgetLedger(tmp_path / "budget.json", amount)
    assert list(tmp_path.iterdir()) == []


def test_auth_fixed_target_and_sensitive_content_not_persisted(tmp_path, caplog):
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://upstream.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer upstream-secret"
        assert b"private-prompt" in request.content
        return httpx.Response(200, stream=TrackedStream((b'{"answer":"ok"}',)))

    ledger, app = setup_proxy(tmp_path, handler)
    with TestClient(app) as client:
        assert client.post("/v1/chat/completions", json={}).status_code == 401
        assert client.get("/status").status_code == 401
        assert client.get("/v1/chat/completions", headers=AUTH).status_code == 405
        response = client.post("/v1/chat/completions?api_key=private-query", headers=AUTH,
                               json={"messages": [{"content": "private-prompt"}]})
        assert response.status_code == 200
        assert response.json() == {"answer": "ok"}
        status = client.get("/status", headers=AUTH).json()
        assert status["used"] == 1
        assert status["remaining"] == 2
    assert len(calls) == 1
    persisted = ledger.path.read_text()
    for secret in ("private-prompt", "private-query", "local-secret", "upstream-secret"):
        assert secret not in persisted
        assert secret not in json.dumps(status)
        assert secret not in caplog.text


async def test_concurrent_requests_cannot_exceed_budget(tmp_path):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        # 使预占完成后仍有多个上游请求尚未返回。
        await asyncio.sleep(0.01)
        assert json.loads((tmp_path / "budget.json").read_text())["used"] >= calls
        return httpx.Response(200, stream=TrackedStream((b"{}",)))

    ledger, app = setup_proxy(tmp_path, handler)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            results = await asyncio.gather(*[client.post("/v1/chat/completions", headers=AUTH, json={}) for _ in range(12)])
            assert [response.status_code for response in results].count(200) == 3
            assert [response.status_code for response in results].count(429) == 9
            assert ledger.snapshot()["used"] == calls == 3


def test_threaded_reservations_are_serialized(tmp_path):
    ledger = proxy.BudgetLedger(tmp_path / "budget.json", 10)
    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            assert sum(pool.map(lambda _: ledger.reserve(), range(50))) == 10
        assert ledger.snapshot()["used"] == 10
    finally:
        ledger.close()


@pytest.mark.parametrize("failure_mode", ["http", "network", "redirect"])
def test_upstream_failure_consumes_budget_without_retry_or_error_disclosure(tmp_path, failure_mode, caplog):
    calls = 0
    stream = TrackedStream((b"private-upstream-error",))

    def handler(request):
        nonlocal calls
        calls += 1
        if failure_mode == "network":
            raise httpx.ConnectError("private-upstream-error", request=request)
        return httpx.Response(302 if failure_mode == "redirect" else 500,
                              headers={"location": "https://other.example"}, stream=stream)

    ledger, app = setup_proxy(tmp_path, handler, max_calls=1)
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", headers=AUTH, json={})
        assert response.status_code == 502
        assert "private" not in response.text
        assert client.post("/v1/chat/completions", headers=AUTH, json={}).status_code == 429
        assert ledger.snapshot()["used"] == 1
    assert calls == 1
    assert failure_mode == "network" or stream.closed
    assert "private-upstream-error" not in ledger.path.read_text() + caplog.text


def test_ledger_reopen_keeps_spend_and_rejects_budget_change_or_second_owner(tmp_path):
    path = tmp_path / "budget.json"
    first = proxy.BudgetLedger(path, 2)
    try:
        first.reserve()
        with pytest.raises(RuntimeError):
            proxy.BudgetLedger(path, 2)
    finally:
        first.close()
    with pytest.raises(RuntimeError):
        proxy.BudgetLedger(path, 3)
    second = proxy.BudgetLedger(path, 2)
    try:
        assert second.snapshot()["used"] == 1
        assert second.reserve()
        assert not second.reserve()
    finally:
        second.close()


def test_ledger_exclusive_across_processes(tmp_path):
    path = tmp_path / "budget.json"
    ledger = proxy.BudgetLedger(path, 2)
    code = ("import importlib.util,pathlib,sys; "
            "s=importlib.util.spec_from_file_location('proxy',sys.argv[1]); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "m.BudgetLedger(pathlib.Path(sys.argv[2]),2)")
    try:
        result = subprocess.run([sys.executable, "-c", code, str(SCRIPT), str(path)], capture_output=True)
        assert result.returncode != 0
        assert b"cannot acquire" in result.stderr
    finally:
        ledger.close()


def test_sse_bytes_and_upstream_close(tmp_path):
    stream = TrackedStream()
    ledger, app = setup_proxy(tmp_path, lambda request: httpx.Response(200,
        headers={"content-type": "text/event-stream"}, stream=stream))
    with TestClient(app) as client:
        response = client.post("/v1/chat/completions", headers=AUTH, json={"stream": True})
        assert response.content == b"".join(stream.chunks)
        assert response.headers["content-type"].startswith("text/event-stream")
        assert stream.closed
        assert ledger.snapshot()["last_status"] == "completed"


@pytest.mark.parametrize("disconnect_on", ["http.response.start", "http.response.body"])
@pytest.mark.parametrize("close_fails", [False, True])
async def test_downstream_disconnect_closes_upstream_without_refund(tmp_path, disconnect_on, close_fails):
    class DisconnectStream(TrackedStream):
        async def aclose(self):
            self.closed = True
            if close_fails:
                raise RuntimeError("private-upstream-close-error")

    stream = DisconnectStream(pause=True)
    ledger, app = setup_proxy(tmp_path, lambda request: httpx.Response(200,
        headers={"content-type": "text/event-stream"}, stream=stream))
    sent = asyncio.Event()
    body_read = False

    async def receive():
        nonlocal body_read
        if not body_read:
            body_read = True
            return {"type": "http.request", "body": b"{}", "more_body": False}
        await sent.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == disconnect_on:
            sent.set()
            await asyncio.sleep(0.01)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/v1/chat/completions",
             "raw_path": b"/v1/chat/completions", "query_string": b"", "root_path": "",
             "headers": [(b"authorization", b"Bearer local-secret")],
             "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 18001)}
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(app(scope, receive, send), timeout=2)
        assert stream.closed
        assert ledger.snapshot()["used"] == 1
        assert ledger.snapshot()["last_status"] == "cancelled"


def test_failed_persistence_prevents_forwarding(tmp_path, monkeypatch):
    calls = []
    ledger, app = setup_proxy(tmp_path, lambda request: calls.append(request))
    monkeypatch.setattr(proxy.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("private-disk-error")))
    with TestClient(app) as client:
        for _ in range(2):
            response = client.post("/v1/chat/completions", headers=AUTH, json={})
            assert response.status_code == 503
            assert "private-disk-error" not in response.text
        assert not ledger.snapshot()["available"]
    assert calls == []


def test_failed_stream_is_closed_and_does_not_refund(tmp_path):
    class BrokenStream(TrackedStream):
        async def __aiter__(self):
            yield b"data: first\n\n"
            raise httpx.ReadError("private-stream-error")

    stream = BrokenStream()
    ledger, app = setup_proxy(tmp_path, lambda request: httpx.Response(200,
        headers={"content-type": "text/event-stream"}, stream=stream))
    with TestClient(app) as client:
        with pytest.raises(RuntimeError, match="acceptance upstream stream failed"):
            client.post("/v1/chat/completions", headers=AUTH, json={})
        assert stream.closed
        assert ledger.snapshot()["used"] == 1
        assert ledger.snapshot()["last_status"] == "failed"
    assert "private-stream-error" not in ledger.path.read_text()


async def test_cancellation_during_upstream_send_consumes_budget(tmp_path):
    started = asyncio.Event()

    async def handler(request):
        started.set()
        await asyncio.Event().wait()

    ledger, app = setup_proxy(tmp_path, handler)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            task = asyncio.create_task(client.post("/v1/chat/completions", headers=AUTH, json={}))
            await started.wait()
            task.cancel("private-cancel-reason")
            with pytest.raises(asyncio.CancelledError) as captured:
                await task
            assert "private-cancel-reason" not in "".join(traceback.format_exception(captured.value))
            assert ledger.snapshot()["used"] == 1
            assert ledger.snapshot()["last_status"] == "cancelled"


@pytest.mark.parametrize("response_races_with_disconnect", [False, True])
@pytest.mark.parametrize("close_fails", [False, True])
async def test_asgi_disconnect_while_waiting_for_headers_cancels_upstream(tmp_path, response_races_with_disconnect, close_fails):
    started = asyncio.Event()
    cancelled = asyncio.Event()
    class HeaderRaceStream(TrackedStream):
        async def aclose(self):
            self.closed = True
            if close_fails:
                raise RuntimeError("private-upstream-close-error")

    stream = HeaderRaceStream()
    body_read = False

    async def handler(request):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            if response_races_with_disconnect:
                # 模拟断线取消到达时，上游恰好已经交回响应句柄，仍应关闭该句柄。
                return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
            raise
        finally:
            cancelled.set()

    async def receive():
        nonlocal body_read
        if not body_read:
            body_read = True
            return {"type": "http.request", "body": b"{}", "more_body": False}
        # 真实 ASGI 断线通知发生在供应商尚未返回响应头时，不用 task.cancel 替代。
        await started.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        pass

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.0"},
             "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/v1/chat/completions",
             "raw_path": b"/v1/chat/completions", "query_string": b"", "root_path": "",
             "headers": [(b"authorization", b"Bearer local-secret")],
             "client": ("127.0.0.1", 1234), "server": ("127.0.0.1", 18001)}
    ledger, app = setup_proxy(tmp_path, handler)
    async with app.router.lifespan_context(app):
        # 超时只作为测试失败上限；成功必须由 http.disconnect 主动取消上游。
        await asyncio.wait_for(app(scope, receive, send), timeout=0.5)
        assert cancelled.is_set()
        assert not response_races_with_disconnect or stream.closed
        assert ledger.snapshot()["used"] == 1
        assert ledger.snapshot()["last_status"] == "cancelled"


@pytest.mark.parametrize("status_code", [200, 500, 302])
def test_upstream_close_failure_never_exposes_private_exception(tmp_path, status_code, caplog):
    class BadClose(TrackedStream):
        async def aclose(self):
            self.closed = True
            raise RuntimeError("private-upstream-close-error")

    stream = BadClose()
    ledger, app = setup_proxy(tmp_path, lambda request: httpx.Response(status_code,
        headers={"content-type": "text/event-stream"}, stream=stream))
    with TestClient(app) as client:
        try:
            response = client.post("/v1/chat/completions", headers=AUTH, json={})
        except Exception as exc:
            assert "private-upstream-close-error" not in "".join(traceback.format_exception(exc))
            assert status_code == 200
        else:
            assert response.status_code == 502
            assert "private-upstream-close-error" not in response.text
        assert stream.closed
        assert ledger.snapshot()["used"] == 1
        assert ledger.snapshot()["last_status"] == "failed"
    assert "private-upstream-close-error" not in ledger.path.read_text() + caplog.text


def test_client_shutdown_failure_does_not_escape_and_releases_ledger(tmp_path, caplog):
    class BadTransport(httpx.MockTransport):
        async def aclose(self):
            raise RuntimeError("private-transport-close-error")

    path = tmp_path / "budget.json"
    ledger = proxy.BudgetLedger(path, 1)
    app = proxy.create_app(ledger=ledger, upstream_base_url="https://upstream.example/v1",
        upstream_api_key="upstream-secret", local_token="local-secret",
        transport=BadTransport(lambda request: httpx.Response(200, stream=TrackedStream())))
    with TestClient(app) as client:
        assert client.get("/status", headers=AUTH).status_code == 200
    assert "private-transport-close-error" not in caplog.text
    reopened = proxy.BudgetLedger(path, 1)
    try:
        assert reopened.snapshot()["used"] == 0
    finally:
        reopened.close()
