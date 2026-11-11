"""验收运行器只使用 Fake HTTP/SSE；不访问真实业务服务、模型或配置。"""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import httpx
import pytest

from ai_service.models.structured_edit import StructuredEditIntent


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "structured_edit_acceptance.py"
SPEC = importlib.util.spec_from_file_location("structured_edit_acceptance", SCRIPT)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
REQUEST_ID = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("branch", ["html", "multi_file", "vue_project"])
def test_task_summary_preserves_java_generation_type(branch):
    assert runner.task_summary({"codeGenType": branch})["codeGenType"] == branch
    assert runner.task_summary({"codeGenType": "private-type"})["codeGenType"] is None


def test_default_dry_run_without_packages_files_or_network(tmp_path):
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), "--session-file", str(tmp_path / "absent.json"),
                             "--message-file", str(tmp_path / "absent.txt"), "--output", str(tmp_path / "report.json")],
                            cwd=tmp_path, capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {"mode": "dry-run", "networkRequests": 0, "configurationRead": False}
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("url", ["https://example.com/api", "http://localhost.evil/api", "http://user:secret@localhost/api", "http://localhost/api?token=secret"])
def test_rejects_nonlocal_or_credential_urls(url):
    with pytest.raises(ValueError, match="LOCAL_URL_REQUIRED"):
        runner.local_url(url)


def test_sse_keeps_counts_not_source_and_requires_frame_boundary():
    summary = runner.SseSummary()
    for line in ['data: {"d":"私有源码😀"}', '', 'event: done', 'data:', '']:
        summary.feed_line(line)
    result = summary.finish()
    assert result["doneCount"] == 1
    assert result["characterCount"] == 6
    assert "私有源码" not in json.dumps(result, ensure_ascii=False)
    unfinished = runner.SseSummary()
    for line in ['event: done', 'data:']:
        unfinished.feed_line(line)
    assert unfinished.finish()["doneCount"] == 0
    assert unfinished.finish()["incompleteFrame"] is True


def fake_transport(events, requests, used_before=2):
    reads = 0

    def handle(request):
        nonlocal reads
        requests.append(request)
        if request.url.port == 18001:
            assert "cookie" not in request.headers
            assert request.headers["authorization"] == "Bearer proxy-secret"
            reads += 1
            return httpx.Response(200, json={"used": used_before if reads == 1 else min(used_before + 3, 60),
                                           "max_calls": 60, "available": True, "secret": "private-status"})
        assert request.headers["cookie"] == "SESSION=cookie-secret"
        assert "authorization" not in request.headers
        if request.url.path.endswith("/task"):
            return httpx.Response(200, json={"code": 0, "data": {"requestId": REQUEST_ID,
                "executionState": "SUCCEEDED", "publicationState": "COMMITTED", "heartbeatState": "TERMINAL",
                "prompt": "private-prompt", "source": "private-source"}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=events)

    return httpx.MockTransport(handle)


def test_real_contract_canonical_and_snapshot_fields_with_fake_transport():
    intent = StructuredEditIntent(version="1", selector="body > h1:nth-of-type(1)", property="color", value="#123456")
    requests = []
    report = runner.run_scenario(app_id=123, message=intent.canonical_prompt(), intent=intent.model_dump(),
        cookies={"SESSION": "cookie-secret"}, proxy_token="proxy-secret",
        transport=fake_transport('data: {"d":"private-source"}\n\nevent: done\ndata:\n\n', requests))
    assert report["attempted"] is True
    assert report["sse"]["doneCount"] == 1
    assert report["usedDelta"] == 3
    assert report["task"]["executionState"] == "SUCCEEDED"
    assert report["requestId"] == REQUEST_ID
    assert report["requestIdSource"] == "latest-task-query-unverified"
    generation = next(request for request in requests if request.url.path.endswith("/code"))
    assert generation.url.params["message"] == intent.canonical_prompt()
    assert json.loads(generation.url.params["editIntent"]) == intent.model_dump()
    for secret in ("cookie-secret", "proxy-secret", "private-prompt", "private-source", "private-status", intent.canonical_prompt()):
        assert secret not in json.dumps(report, ensure_ascii=False)


def test_fake_business_error_redacts_message_and_correlates_request():
    requests = []
    event = 'event: business-error\ndata: ' + json.dumps({"errorCode": "GENERATION_CANCELLED", "requestId": REQUEST_ID,
                                                          "message": "cookie-secret private-source"}) + '\n\n'
    report = runner.run_scenario(app_id=123, message="private-prompt", cookies={"SESSION": "cookie-secret"},
                                proxy_token="proxy-secret", transport=fake_transport(event, requests))
    assert report["sse"]["businessErrorCount"] == 1
    assert report["sse"]["errorCode"] == "GENERATION_CANCELLED"
    assert report["requestIdSource"] == "business-error"
    assert "private-source" not in json.dumps(report)


def test_exhausted_budget_makes_no_spring_request():
    requests = []
    report = runner.run_scenario(app_id=123, message="hello", cookies={"SESSION": "cookie-secret"},
                                proxy_token="proxy-secret", transport=fake_transport("", requests, used_before=60))
    assert report["attempted"] is False
    assert report["error"] == "ACCEPTANCE_BUDGET_EXHAUSTED"
    assert all(request.url.port == 18001 for request in requests)


def test_network_error_does_not_echo_sensitive_request_url():
    def handler(request):
        raise httpx.ConnectError("https://secret.example/?token=proxy-secret", request=request)

    report = runner.run_scenario(app_id=123, message="private-prompt", cookies={"SESSION": "cookie-secret"},
                                proxy_token="proxy-secret", transport=httpx.MockTransport(handler))
    assert report["error"] == "REQUEST_FAILED"
    assert report["attempted"] is False
    assert "secret" not in json.dumps(report)


def test_output_exists_fails_before_session_read_and_remains_unchanged(tmp_path, monkeypatch, capsys):
    output = tmp_path / "report.json"
    output.write_text('{"existing":true}', encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner.subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0))
    code = runner.main(["--execute", "--app-id", "123", "--output", str(output),
                        "--session-file", str(tmp_path / "absent.json"), "--message-file", str(tmp_path / "absent.txt")])
    assert code == 1
    assert "OUTPUT_ALREADY_EXISTS" in capsys.readouterr().out
    assert output.read_text(encoding="utf-8") == '{"existing":true}'


def test_cancel_uses_separate_spring_post_and_does_not_end_stream_early():
    requests = []

    class SlowStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"d":"private-source"}\n\n'
            time.sleep(0.08)
            yield ('event: business-error\ndata: ' + json.dumps({"errorCode": "GENERATION_CANCELLED",
                "requestId": REQUEST_ID}) + '\n\n').encode()

    def handler(request):
        requests.append(request)
        if request.url.port == 18001:
            return httpx.Response(200, json={"used": 2, "max_calls": 60, "available": True})
        if request.url.path.endswith("/cancel"):
            assert request.method == "POST"
            assert json.loads(request.content) == {"appId": 123}
            assert request.headers["cookie"] == "SESSION=cookie-secret"
            return httpx.Response(200, json={"code": 0, "data": {"state": "CANCELLING", "secret": "private-source"}})
        if request.url.path.endswith("/task"):
            return httpx.Response(200, json={"code": 0, "data": {"requestId": REQUEST_ID, "executionState": "CANCELLED"}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=SlowStream())

    report = runner.run_scenario(app_id=123, message="private-prompt", cookies={"SESSION": "cookie-secret"},
                                proxy_token="proxy-secret", cancel_after_seconds=0.01, transport=httpx.MockTransport(handler))
    assert report["cancel"] == {"requested": True, "httpStatus": 200, "code": 0, "state": "CANCELLING"}
    assert report["sse"]["businessErrorCount"] == 1
    assert report["task"]["executionState"] == "CANCELLED"
    assert "private-source" not in json.dumps(report)
