from __future__ import annotations

import asyncio
import logging
import math
from datetime import UTC, datetime
from queue import Empty, Full, Queue
from threading import Event, Thread
from time import monotonic
from typing import Any

from ai_service.config import Settings

logger = logging.getLogger(__name__)

_ALLOWED_METADATA = {
    "request_id",
    "app_id",
    "engine",
    "code_gen_type",
    "node",
    "status",
    "error_code",
    "repair_count",
    "tool_call_count",
}
_MAX_VALUE_LENGTH = 128


def _install_sdk_log_redaction() -> None:
    """SDK 会在错误回调之前记录异常；同时覆盖之后创建的 SDK logger。"""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_langsmith_redacted", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if record.name == "langsmith" or record.name.startswith("langsmith."):
            category = type(record.exc_info[1]).__name__ if record.exc_info else "SDK_LOG"
            record.msg = "LangSmith SDK event (%s)"
            record.args = (category[:_MAX_VALUE_LENGTH],)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return record

    factory._langsmith_redacted = True
    logging.setLogRecordFactory(factory)


def _safe_metadata(values: dict[str, Any]) -> dict[str, str | int | float | bool]:
    """只保留有限、可审计的标量元数据，拒绝 prompt/artifact 等业务正文。"""
    safe: dict[str, str | int | float | bool] = {}
    for key, value in values.items():
        if key not in _ALLOWED_METADATA or value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            if isinstance(value, float) and not math.isfinite(value):
                continue
            safe[key] = value if not isinstance(value, str) else value[:_MAX_VALUE_LENGTH]
    return safe


class LangSmithTracer:
    """可选的脱敏旁路追踪器；任何追踪错误都不能影响生成终态。"""

    def __init__(self, settings: Settings, *, queue_size: int = 128, shutdown_timeout: float = 1.0) -> None:
        self.enabled = bool(
            settings.langsmith_tracing
            and settings.langsmith_api_key
            and settings.langsmith_endpoint
        )
        self._client: Any | None = None
        self._project = settings.langsmith_project
        self._queue: Queue[dict[str, Any]] = Queue(maxsize=queue_size)
        self._closed = Event()
        self._stopped = Event()
        self._shutdown_timeout = shutdown_timeout
        self._shutdown_deadline: float | None = None
        self._worker: Thread | None = None
        if not self.enabled:
            return
        try:
            from langsmith import Client
            from langsmith.run_trees import RunTree
            from urllib3.util.retry import Retry

            _install_sdk_log_redaction()
            self._run_tree = RunTree

            self._client = Client(
                api_key=settings.langsmith_api_key,
                api_url=str(settings.langsmith_endpoint),
                auto_batch_tracing=False,
                timeout_ms=(1000, 1000),
                retry_config=Retry(total=0),
                info={"version": "0.12.4"},
                hide_inputs=True,
                hide_outputs=True,
                tracing_error_callback=lambda exc: logger.warning(
                    "LangSmith tracing failed: %s", type(exc).__name__
                ),
            )
            self._worker = Thread(target=self._send_records, name="langsmith-metadata", daemon=True)
            self._worker.start()
        except Exception as exc:  # pragma: no cover - 依赖可选 SDK 和运行时环境
            self.enabled = False
            logger.warning("LangSmith tracing disabled: client initialization failed (%s)", type(exc).__name__)

    def _send_records(self) -> None:
        try:
            while not self._closed.is_set() or not self._queue.empty():
                if self._shutdown_deadline is not None and monotonic() >= self._shutdown_deadline:
                    while True:
                        try:
                            self._queue.get_nowait()
                            self._queue.task_done()
                        except Empty:
                            break
                    break
                try:
                    values = self._queue.get(timeout=0.05)
                except Empty:
                    continue
                try:
                    run = self._run_tree(
                        name=values["name"],
                        run_type="chain",
                        project_name=self._project[:_MAX_VALUE_LENGTH],
                        inputs={},
                        serialized={"name": values["name"]},
                        start_time=values["started_at"],
                        ls_client=self._client,
                    )
                    run.end(end_time=values["ended_at"], metadata=values["metadata"])
                    run.post()
                except Exception as exc:
                    logger.warning("LangSmith tracing failed: %s", type(exc).__name__)
                finally:
                    self._queue.task_done()
        finally:
            try:
                if self._client is not None:
                    self._client.close(timeout=0)
            except Exception as exc:
                logger.warning("LangSmith tracing close failed: %s", type(exc).__name__)
            self._stopped.set()

    async def record(
        self,
        *,
        name: str,
        metadata: dict[str, Any],
        status: str,
        error_code: str | None = None,
        started_at: datetime | None = None,
    ) -> None:
        if not self.enabled or self._client is None or self._closed.is_set():
            return
        try:
            safe = _safe_metadata({**metadata, "status": status, "error_code": error_code})
            ended = datetime.now(UTC)
            self._queue.put_nowait({
                "name": name[:_MAX_VALUE_LENGTH],
                "metadata": safe,
                "started_at": started_at or ended,
                "ended_at": ended,
            })
        except Full:
            pass  # 观测队列饱和时丢弃记录，不能阻塞业务或累积无界任务。
        except Exception as exc:  # pragma: no cover - 依赖网络和供应商服务
            logger.warning("LangSmith tracing failed: %s", type(exc).__name__)

    async def close(self) -> None:
        if self._shutdown_deadline is None:
            self._shutdown_deadline = monotonic() + self._shutdown_timeout
        self._closed.set()
        if self._worker is None:
            return
        deadline = monotonic() + self._shutdown_timeout
        while not self._stopped.is_set() and monotonic() < deadline:
            await asyncio.sleep(min(0.01, max(0.0, deadline - monotonic())))
