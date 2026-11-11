"""单应用、单场景的本地验收运行器；默认 dry-run 不读取配置或访问网络。

在 ai-service 目录使用 uv run python ../scripts/structured_edit_acceptance.py。
只调用 Spring 正常生成入口及预算代理状态，不创建应用、不直连模型、不重试。
报告只表示 HTTP/SSE 和台账观测，不能替代页面属性、图片、操作与预览验收。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid


ROOT = Path(__file__).resolve().parents[1]
SPRING_BASE = "http://localhost:8123/api"
PROXY_STATUS = "http://127.0.0.1:18001/status"


def local_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("LOCAL_URL_REQUIRED")
    return value.rstrip("/")


def stable_code(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", value) else None


def request_id(value):
    try:
        return str(uuid.UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


class SseSummary:
    """只保留单帧有界 JSON；不累积流式源码或输出服务端自由文本。"""

    def __init__(self):
        self.event = "message"
        self.data = []
        self.frame_size = 0
        self.result = dict(doneCount=0, businessErrorCount=0, characterCount=0,
                           characterUnit="UTF-16", errorCode=None, requestId=None,
                           invalidFrameCount=0)

    def feed_line(self, line: str):
        if not line:
            self._dispatch()
        elif line.startswith("event:"):
            self.event = line[6:].lstrip(" ")
        elif line.startswith("data:"):
            value = line[5:]
            if value.startswith(" "):
                value = value[1:]
            self.frame_size += len(value)
            if self.frame_size > 2 * 1024 * 1024:
                raise ValueError("SSE_FRAME_TOO_LARGE")
            self.data.append(value)

    def _dispatch(self):
        if not self.data:
            self.event = "message"
            return
        payload = "\n".join(self.data)
        if self.event == "done":
            self.result["doneCount"] += 1
        else:
            try:
                parsed = json.loads(payload)
                if not isinstance(parsed, dict):
                    raise ValueError()
            except ValueError:
                parsed = {}
                self.result["invalidFrameCount"] += 1
            if self.event == "business-error":
                self.result["businessErrorCount"] += 1
                self.result["errorCode"] = stable_code(parsed.get("errorCode")) or "INVALID_BUSINESS_ERROR"
                self.result["requestId"] = request_id(parsed.get("requestId"))
            elif self.event == "message" and isinstance(parsed.get("d"), str):
                self.result["characterCount"] += len(parsed["d"].encode("utf-16-le", errors="surrogatepass")) // 2
        self.event, self.data, self.frame_size = "message", [], 0

    def finish(self):
        # SSE 仅在空行后分发；流末尾未完成的帧不能伪装成成功终态。
        result = dict(self.result)
        result["incompleteFrame"] = bool(self.data)
        result["terminalCount"] = result["doneCount"] + result["businessErrorCount"]
        return result


def task_summary(value):
    if not isinstance(value, dict):
        return None
    result = {"requestId": request_id(value.get("requestId"))}
    # Java 任务台账使用小写生成类型，不能复用只接受大写错误码的过滤器。
    result["codeGenType"] = value.get("codeGenType") if value.get("codeGenType") in ("html", "multi_file", "vue_project") else None
    for key in ("executionState", "errorCode", "leaseState", "publicationState", "heartbeatState"):
        result[key] = stable_code(value.get(key))
    return result


def run_scenario(*, app_id: int, message: str, cookies: dict, proxy_token: str,
                 intent=None, spring_base=SPRING_BASE, proxy_status=PROXY_STATUS,
                 cancel_after_seconds=None, transport=None):
    """transport 仅供 Fake 测试；凭据分向发送，禁止重定向和隐式环境代理。"""
    import httpx

    spring_base, proxy_status = local_url(spring_base), local_url(proxy_status)
    if type(app_id) is not int or app_id <= 0 or not isinstance(message, str) or not message.strip():
        raise ValueError("INVALID_SCENARIO")
    if not isinstance(cookies, dict) or not cookies or not proxy_token:
        raise ValueError("SESSION_REQUIRED")
    if cancel_after_seconds is not None and not 0 < cancel_after_seconds <= 3600:
        raise ValueError("INVALID_CANCEL_DELAY")
    params = {"appId": str(app_id), "message": message}
    if intent is not None:
        from ai_service.models.structured_edit import StructuredEditIntent

        validated = StructuredEditIntent.model_validate(intent)
        if validated.canonical_prompt() != message:
            raise ValueError("STRUCTURED_EDIT_PROMPT_MISMATCH")
        params["editIntent"] = json.dumps(validated.model_dump(), ensure_ascii=False, separators=(",", ":"))

    report = dict(appId=app_id, attempted=False, observation="http-sse-and-latest-task",
                  elapsedSeconds=0, budgetBefore=None, budgetAfter=None, usedDelta=None,
                  sse=None, task=None, requestId=None, requestIdSource=None, error=None,
                  cancel={"requested": False, "httpStatus": None, "code": None, "state": None})
    summary = SseSummary()
    stopped = threading.Event()
    timer = None
    start = time.monotonic()

    def budget(client):
        response = client.get(proxy_status, headers={"Authorization": "Bearer " + proxy_token}, timeout=10)
        if response.status_code != 200:
            raise ValueError("PROXY_STATUS_FAILED")
        payload = response.json()
        if (not isinstance(payload, dict) or type(payload.get("used")) is not int
                or type(payload.get("max_calls")) is not int
                or not 0 <= payload["used"] <= payload["max_calls"] <= 60
                or payload.get("available") is not True):
            raise ValueError("PROXY_STATUS_INVALID")
        return {"used": payload["used"], "maxCalls": payload["max_calls"],
                "remaining": payload["max_calls"] - payload["used"]}

    def cancel():
        if stopped.is_set():
            return
        report["cancel"]["requested"] = True
        try:
            with httpx.Client(cookies=cookies, transport=transport, follow_redirects=False,
                              trust_env=False, timeout=15) as client:
                response = client.post(spring_base + "/apps/chat/gen/code/cancel", json={"appId": app_id})
                report["cancel"]["httpStatus"] = response.status_code
                payload = response.json()
                if isinstance(payload, dict):
                    report["cancel"]["code"] = payload.get("code") if type(payload.get("code")) is int else None
                    data = payload.get("data")
                    report["cancel"]["state"] = stable_code(data.get("state")) if isinstance(data, dict) else None
        except Exception:
            report["cancel"]["error"] = "CANCEL_REQUEST_FAILED"

    # 独立客户端防止无 domain 的测试会话 cookie 被送到预算代理。
    with httpx.Client(transport=transport, follow_redirects=False, trust_env=False, timeout=10) as proxy_client, \
            httpx.Client(cookies=cookies, transport=transport, follow_redirects=False,
                         trust_env=False, timeout=httpx.Timeout(600, connect=10)) as spring_client:
        try:
            report["budgetBefore"] = budget(proxy_client)
            if report["budgetBefore"]["remaining"] <= 0:
                raise ValueError("ACCEPTANCE_BUDGET_EXHAUSTED")
            report["attempted"] = True
            if cancel_after_seconds is not None:
                timer = threading.Timer(cancel_after_seconds, cancel)
                timer.start()
            with spring_client.stream("GET", spring_base + "/apps/chat/gen/code", params=params,
                                      headers={"Accept": "text/event-stream"}) as response:
                report["httpStatus"] = response.status_code
                if response.status_code != 200:
                    raise ValueError("GENERATION_HTTP_FAILED")
                if "text/event-stream" not in response.headers.get("content-type", "").lower():
                    raise ValueError("GENERATION_NOT_SSE")
                for line in response.iter_lines():
                    summary.feed_line(line)
        except Exception as exc:
            report["error"] = str(exc) if isinstance(exc, ValueError) and stable_code(str(exc)) else "REQUEST_FAILED"
        finally:
            stopped.set()
            if timer:
                timer.cancel()
                timer.join()
            report["elapsedSeconds"] = round(time.monotonic() - start, 3)
            report["sse"] = summary.finish()
            if report["attempted"]:
                try:
                    response = spring_client.get(spring_base + "/apps/chat/gen/code/task", params={"appId": app_id}, timeout=15)
                    payload = response.json()
                    if response.status_code != 200 or not isinstance(payload, dict) or payload.get("code") != 0:
                        raise ValueError()
                    report["task"] = task_summary(payload.get("data"))
                except Exception:
                    report["taskQueryError"] = "TASK_QUERY_FAILED"
            try:
                report["budgetAfter"] = budget(proxy_client)
                if report["budgetBefore"]:
                    report["usedDelta"] = report["budgetAfter"]["used"] - report["budgetBefore"]["used"]
            except Exception:
                report["budgetQueryError"] = "PROXY_STATUS_FAILED"
    report["requestId"] = report["sse"]["requestId"]
    report["requestIdSource"] = "business-error" if report["requestId"] else None
    if not report["requestId"] and report["task"]:
        # 最新台账不是原子快照，入口拒绝时可能仍是旧任务；不能无条件视作本次请求。
        report["requestId"] = report["task"]["requestId"]
        report["requestIdSource"] = "latest-task-query-unverified"
    return report


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--session-file", type=Path)
    parser.add_argument("--app-id", type=int)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--message-file", type=Path)
    group.add_argument("--intent-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cancel-after-seconds", type=float)
    parser.add_argument("--spring-base-url", default=SPRING_BASE)
    parser.add_argument("--proxy-status-url", default=PROXY_STATUS)
    return parser.parse_args(argv)


def main(argv=None):
    options = parse_arguments(argv)
    if not options.execute:
        print(json.dumps({"mode": "dry-run", "networkRequests": 0, "configurationRead": False}))
        return 0
    try:
        if (not options.session_file or not options.session_file.is_absolute()
                or not options.output or not options.app_id
                or not (options.message_file or options.intent_file)):
            raise ValueError("EXECUTE_ARGUMENTS_REQUIRED")
        local_url(options.spring_base_url)
        local_url(options.proxy_status_url)
        output = options.output.resolve()
        relative = output.relative_to(ROOT)
        if output.suffix != ".json" or subprocess.run(
                ["git", "check-ignore", "--quiet", "--", str(relative)], cwd=ROOT,
                capture_output=True, check=False).returncode != 0:
            raise ValueError("IGNORED_JSON_OUTPUT_REQUIRED")
        if output.exists():
            raise ValueError("OUTPUT_ALREADY_EXISTS")
        session = json.loads(options.session_file.read_text(encoding="utf-8-sig"))
        cookies, token = session.get("cookies"), session.get("proxyToken")
        if (not isinstance(cookies, dict) or not cookies or not isinstance(token, str) or not token
                or not all(isinstance(key, str) and isinstance(value, str) for key, value in cookies.items())):
            raise ValueError("INVALID_SESSION_FILE")
        intent = None
        if options.intent_file:
            from ai_service.models.structured_edit import StructuredEditIntent

            intent = StructuredEditIntent.model_validate_json(options.intent_file.read_text(encoding="utf-8-sig"))
            message, intent = intent.canonical_prompt(), intent.model_dump()
        else:
            message = options.message_file.read_text(encoding="utf-8-sig")
        output.parent.mkdir(parents=True, exist_ok=True)
        # 先独占占位，避免请求已经消耗预算之后才发现不能保存报告或文件重名。
        with output.open("x", encoding="utf-8") as destination:
            report = run_scenario(app_id=options.app_id, message=message, cookies=cookies,
                                  proxy_token=token, intent=intent, spring_base=options.spring_base_url,
                                  proxy_status=options.proxy_status_url,
                                  cancel_after_seconds=options.cancel_after_seconds)
            json.dump(report, destination, ensure_ascii=False, indent=2)
            destination.write("\n")
        print(json.dumps({"mode": "measured", "output": str(output), "usedDelta": report["usedDelta"]}))
        return 0 if (report["error"] is None and report["sse"]["terminalCount"] == 1
                     and report["sse"]["doneCount"] == 1) else 1
    except Exception as exc:
        # 不回显 httpx 请求 URL、Pydantic 输入、文件正文或任何凭据。
        print(json.dumps({"mode": "failed", "error": str(exc)
                          if isinstance(exc, ValueError) and stable_code(str(exc)) else "ACCEPTANCE_RUNNER_FAILED"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
