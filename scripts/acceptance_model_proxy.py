"""仅供本地真实验收的供应商请求硬预算代理，不属于生产请求链路。

在 ai-service 目录运行：
  uv run python ../scripts/acceptance_model_proxy.py
  uv run python ../scripts/acceptance_model_proxy.py --execute --max-calls 60 \
      --ledger D:/absolute/ignored/acceptance-ledger.json --port 18001

默认仅输出 dry-run 提示，不读取 Settings/.env、不创建账本、不访问网络。
执行前通过环境 ACCEPTANCE_PROXY_TOKEN 提供随机本地令牌；上游凭据读取
ai_service.config.Settings。AI 服务使用 http://127.0.0.1:18001/v1 和本地令牌。
同一轮验收必须始终使用同一账本；不要删除、编辑账本或另建账本绕过总额度。
计数表示已持久化的发送许可；崩溃窗口可能高估实际请求，绝不退款。
"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import threading
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BudgetLedger:
    """进程锁负责独占账本，线程锁串行化所有预占及状态写入。"""

    def __init__(self, path: Path, max_calls: int):
        if not path.is_absolute() or not 1 <= max_calls <= 60:
            raise ValueError("ledger must be absolute and max_calls must be 1..60")
        self.path = path.resolve()
        self._mutex = threading.Lock()
        self._lock_file = None
        self._unavailable = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_file = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
            self._lock_file.seek(0, os.SEEK_END)
            if self._lock_file.tell() == 0:
                self._lock_file.write(b"0")
                self._lock_file.flush()
            self._lock_file.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.path.exists():
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
                expected = {"version", "max_calls", "used", "created_at", "updated_at", "last_status"}
                if (set(self._data) != expected or self._data["version"] != 1
                        or self._data["max_calls"] != max_calls
                        or type(self._data["used"]) is not int
                        or not 0 <= self._data["used"] <= max_calls
                        or self._data["last_status"] not in {"ready", "reserved", "completed", "failed", "cancelled"}
                        or not all(isinstance(self._data[key], str) for key in ("created_at", "updated_at"))):
                    raise ValueError("invalid ledger or changed budget")
            else:
                self._data = dict(version=1, max_calls=max_calls, used=0,
                                  created_at=_now(), updated_at=_now(), last_status="ready")
                self._persist()
        except BaseException:
            self.close()
            raise RuntimeError("cannot acquire or initialize acceptance ledger") from None

    def _persist(self) -> None:
        # 先刷盘再原子替换；写入失败后本进程永久拒绝新许可，避免不确定状态继续发送。
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as output:
                json.dump(self._data, output, ensure_ascii=True)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        except Exception:
            self._unavailable = True
            raise RuntimeError("acceptance ledger write failed") from None

    def reserve(self) -> bool:
        with self._mutex:
            if self._unavailable:
                raise RuntimeError("acceptance ledger unavailable")
            if self._data["used"] >= self._data["max_calls"]:
                return False
            self._data.update(used=self._data["used"] + 1, updated_at=_now(), last_status="reserved")
            self._persist()
            return True

    def finish(self, status: str) -> None:
        with self._mutex:
            self._data.update(updated_at=_now(), last_status=status)
            self._persist()

    def snapshot(self) -> dict[str, Any]:
        with self._mutex:
            return {"max_calls": self._data["max_calls"], "used": self._data["used"],
                    "remaining": self._data["max_calls"] - self._data["used"],
                    "last_status": self._data["last_status"], "available": not self._unavailable}

    def close(self) -> None:
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None


def create_app(*, ledger: BudgetLedger, upstream_base_url: str, upstream_api_key: str,
               local_token: str, transport=None):
    # 延迟导入使无执行参数的 CLI 不触发应用配置与运行环境初始化。
    import anyio
    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, StreamingResponse

    if not local_token or not upstream_api_key or upstream_api_key == "not-configured":
        raise ValueError("proxy credentials missing")
    target = httpx.URL(upstream_base_url.rstrip("/") + "/chat/completions")
    if target.scheme not in ("http", "https") or target.username or target.password or target.query or target.fragment:
        raise ValueError("invalid upstream URL")

    async def close_safely(resource):
        # 清理异常也可能携带供应商原文；不记录、不链式传播，也不因此退回调用额度。
        try:
            await resource.aclose()
        except anyio.get_cancelled_exc_class():
            raise anyio.get_cancelled_exc_class() from None
        except Exception:
            return

    @asynccontextmanager
    async def lifespan(app):
        # trust_env=False 禁用隐式代理；零重试和禁止重定向保证一次许可至多发一次请求。
        client = httpx.AsyncClient(transport=transport, follow_redirects=False,
                                  trust_env=False, timeout=300)
        app.state.client = client
        try:
            yield
        finally:
            try:
                with anyio.CancelScope(shield=True):
                    await close_safely(client)
            finally:
                ledger.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authorized(request):
        return hmac.compare_digest(request.headers.get("authorization", "").encode("utf-8"),
                                   ("Bearer " + local_token).encode("utf-8"))

    def failure(status: int, code: str):
        return JSONResponse({"error": {"code": code, "message": code}}, status_code=status)

    async def status(request):
        if not authorized(request):
            return failure(401, "PROXY_UNAUTHORIZED")
        return JSONResponse(ledger.snapshot())

    async def completions(request):
        if not authorized(request):
            return failure(401, "PROXY_UNAUTHORIZED")
        body = await request.body()
        try:
            if not ledger.reserve():
                return failure(429, "ACCEPTANCE_BUDGET_EXHAUSTED")
        except Exception:
            return failure(503, "ACCEPTANCE_LEDGER_UNAVAILABLE")
        upstream = None
        try:
            outbound = app.state.client.build_request("POST", target, content=body,
                headers={"Authorization": "Bearer " + upstream_api_key,
                         "Content-Type": "application/json", "Accept-Encoding": "identity"})
            disconnected = False
            async with anyio.create_task_group() as pending:
                async def watch_disconnect():
                    nonlocal disconnected
                    # 请求体已完整读取；响应头等待期也要消费断线事件，ASGI 不会自动取消处理器。
                    while True:
                        if (await request.receive())["type"] == "http.disconnect":
                            disconnected = True
                            pending.cancel_scope.cancel()
                            return

                pending.start_soon(watch_disconnect)
                upstream = await app.state.client.send(outbound, stream=True)
                # 上游已交回响应，先停止此监听，随后由 StreamingResponse 独占 receive。
                pending.cancel_scope.cancel()
            if disconnected:
                with anyio.CancelScope(shield=True):
                    if upstream is not None:
                        await close_safely(upstream)
                    ledger.finish("cancelled")
                return failure(499, "ACCEPTANCE_CLIENT_DISCONNECTED")
            if not 200 <= upstream.status_code < 300:
                await close_safely(upstream)
                ledger.finish("failed")
                return failure(502, "ACCEPTANCE_UPSTREAM_FAILED")
        except BaseException as exc:
            with anyio.CancelScope(shield=True):
                if upstream is not None:
                    await close_safely(upstream)
                ledger.finish("failed" if isinstance(exc, Exception) else "cancelled")
            if not isinstance(exc, Exception):
                if isinstance(exc, anyio.get_cancelled_exc_class()):
                    raise anyio.get_cancelled_exc_class() from None
                raise
            return failure(502, "ACCEPTANCE_UPSTREAM_FAILED")

        outcome = "cancelled"

        async def chunks():
            nonlocal outcome
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
                outcome = "completed"
            except Exception:
                outcome = "failed"
                raise RuntimeError("acceptance upstream stream failed") from None
            except anyio.get_cancelled_exc_class():
                raise anyio.get_cancelled_exc_class() from None

        class ClosingStreamingResponse(StreamingResponse):
            async def __call__(self, scope, receive, send):
                try:
                    await super().__call__(scope, receive, send)
                except anyio.get_cancelled_exc_class():
                    raise anyio.get_cancelled_exc_class() from None
                finally:
                    # 即使在响应头发送后、生成器启动前断开，也必须释放上游连接。
                    with anyio.CancelScope(shield=True):
                        try:
                            await close_safely(upstream)
                        finally:
                            ledger.finish(outcome)

        media_type = "text/event-stream" if "text/event-stream" in upstream.headers.get("content-type", "") else "application/json"
        return ClosingStreamingResponse(chunks(), status_code=upstream.status_code, media_type=media_type)

    # 内层导入的 Request 在 postponed annotations 下不在模块全局，显式提供类型避免误作查询参数。
    status.__annotations__["request"] = Request
    completions.__annotations__["request"] = Request
    app.get("/status")(status)
    app.post("/v1/chat/completions")(completions)
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--max-calls", type=int, default=60)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--port", type=int, default=18001)
    args = parser.parse_args(argv)
    if not args.execute:
        print("DRY RUN: no settings loaded, no ledger created, no network requests.")
        return 0
    if args.ledger is None or not args.ledger.is_absolute() or not 1 <= args.max_calls <= 60 or not 1 <= args.port <= 65535:
        parser.error("execute requires an absolute --ledger, --max-calls 1..60 and valid --port")
    ledger = None
    try:
        from ai_service.config import Settings
        import uvicorn

        settings = Settings()
        local_token = os.environ.get("ACCEPTANCE_PROXY_TOKEN", "")
        if not local_token:
            raise ValueError("local token missing")
        ledger = BudgetLedger(args.ledger, args.max_calls)
        app = create_app(ledger=ledger, upstream_base_url=settings.model_base_url,
                         upstream_api_key=settings.model_api_key, local_token=local_token)
        uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, log_level="warning")
        return 0
    except Exception:
        # 配置校验与 HTTP 异常可能包含密钥，CLI 只输出固定提示。
        print("Acceptance proxy startup failed; check settings, token and ledger ownership.")
        return 1
    finally:
        if ledger is not None:
            ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
