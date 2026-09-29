from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, Callable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

STATUS_FIELDS = frozenset(
    {
        "node",
        "requestId",
        "appId",
        "codeGenType",
        "qualityPassed",
        "repairCount",
        "toolCallCount",
    }
)
REQUIRED_STATUS_FIELDS = frozenset(
    {"node", "requestId", "appId", "codeGenType", "repairCount", "toolCallCount"}
)

STATUS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ai_workflow_status (
    thread_id text PRIMARY KEY,
    app_id bigint NOT NULL,
    request_id text NOT NULL,
    code_gen_type text NOT NULL,
    node text NOT NULL,
    quality_passed boolean,
    repair_count integer NOT NULL DEFAULT 0,
    tool_call_count integer NOT NULL DEFAULT 0,
    status_payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
)
"""
STATUS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_ai_workflow_status_expires_at
ON ai_workflow_status (expires_at)
"""
STATUS_UPSERT_SQL = """
INSERT INTO ai_workflow_status (
    thread_id, app_id, request_id, code_gen_type, node, quality_passed,
    repair_count, tool_call_count, status_payload, updated_at, expires_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now(), now() + (%s * interval '1 second'))
ON CONFLICT (thread_id) DO UPDATE SET
    app_id = EXCLUDED.app_id,
    request_id = EXCLUDED.request_id,
    code_gen_type = EXCLUDED.code_gen_type,
    node = EXCLUDED.node,
    quality_passed = EXCLUDED.quality_passed,
    repair_count = EXCLUDED.repair_count,
    tool_call_count = EXCLUDED.tool_call_count,
    status_payload = EXCLUDED.status_payload,
    updated_at = now(),
    expires_at = EXCLUDED.expires_at
"""
SCHEMA_CHECK_SQL = """
SELECT
    to_regclass('public.checkpoints') AS checkpoints,
    to_regclass('public.checkpoint_blobs') AS checkpoint_blobs,
    to_regclass('public.checkpoint_writes') AS checkpoint_writes,
    to_regclass('public.ai_workflow_status') AS ai_workflow_status
"""


class PostgresCheckpoint:
    """管理 PostgreSQL checkpointer、脱敏状态摘要和过期清理。"""

    def __init__(
        self,
        url: str,
        *,
        required: bool,
        auto_setup: bool,
        ttl_seconds: int,
        pool_min_size: int,
        pool_max_size: int,
        pool_factory: Callable[..., AsyncConnectionPool] = AsyncConnectionPool,
        saver_factory: Callable[..., AsyncPostgresSaver] = AsyncPostgresSaver,
    ):
        self._pool = pool_factory(
            conninfo=url,
            min_size=pool_min_size,
            max_size=pool_max_size,
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
            },
        )
        self._graph_saver = saver_factory(
            self._pool,
            serde=JsonPlusSerializer(allowed_json_modules=()),
        )
        self._required = required
        self._auto_setup = auto_setup
        self._ttl_seconds = ttl_seconds
        self._cleanup_task: asyncio.Task[None] | None = None
        self.available = False

    async def start(self) -> None:
        """打开连接池并验证 schema；optional 模式失败时显式降级。"""
        try:
            await self._pool.open(wait=True, timeout=10.0)
            if self._auto_setup:
                await self.setup_schema()
            await self._verify_schema()
            self.available = True
            await self.cleanup_expired()
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        except Exception as exc:
            self.available = False
            with suppress(Exception):
                await self._pool.close()
            if self._required:
                raise RuntimeError(
                    "PostgreSQL checkpoint is required but unavailable"
                ) from exc
            logger.warning(
                "PostgreSQL checkpoint unavailable; running without recovery: %s",
                type(exc).__name__,
            )

    async def setup_schema(self) -> None:
        """幂等创建官方 checkpoint 表和本服务的脱敏状态表。"""
        await self._graph_saver.setup()
        async with self._pool.connection() as connection:
            await connection.execute(STATUS_SCHEMA_SQL)
            await connection.execute(STATUS_INDEX_SQL)

    async def close(self) -> None:
        """停止后台清理并关闭连接池。"""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
        await self._pool.close()
        self.available = False

    async def save(self, thread_id: str, state: dict[str, Any]) -> None:
        """只保存字段白名单中的精简状态，并刷新过期时间。"""
        if not self.available:
            return
        normalized = self._normalize_state(state)
        try:
            async with self._pool.connection() as connection:
                await connection.execute(
                    STATUS_UPSERT_SQL,
                    (
                        thread_id,
                        normalized["appId"],
                        normalized["requestId"],
                        normalized["codeGenType"],
                        normalized["node"],
                        normalized.get("qualityPassed"),
                        normalized["repairCount"],
                        normalized["toolCallCount"],
                        Jsonb(normalized),
                        self._ttl_seconds,
                    ),
                )
        except Exception as exc:
            self.available = False
            if self._required:
                raise RuntimeError("PostgreSQL checkpoint write failed") from exc
            logger.warning(
                "PostgreSQL checkpoint write failed; degrading: %s",
                type(exc).__name__,
            )

    async def cleanup_graph(self, thread_id: str) -> None:
        """删除终态图 checkpoint；失败不反转已经确定的业务终态。"""
        if not self.available:
            return
        try:
            await self._graph_saver.adelete_thread(thread_id)
        except Exception as exc:
            self.available = False
            logger.warning(
                "LangGraph PostgreSQL checkpoint cleanup failed for %s: %s",
                thread_id,
                type(exc).__name__,
            )

    async def cleanup_expired(self) -> None:
        """清理过期 thread；图删除失败时保留状态行供下次重试。"""
        if not self.available:
            return
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                "SELECT thread_id FROM ai_workflow_status WHERE expires_at < now()"
            )
            rows = await cursor.fetchall()
            for row in rows:
                thread_id = str(row["thread_id"])
                try:
                    await self._graph_saver.adelete_thread(thread_id)
                    await connection.execute(
                        "DELETE FROM ai_workflow_status WHERE thread_id = %s",
                        (thread_id,),
                    )
                except Exception as exc:
                    logger.warning(
                        "Expired PostgreSQL checkpoint cleanup failed for %s: %s",
                        thread_id,
                        type(exc).__name__,
                    )

    async def ping(self) -> bool:
        """实时探测 PostgreSQL；降级后需要重启服务恢复 saver。"""
        if not self.available:
            return False
        try:
            async with self._pool.connection() as connection:
                await connection.execute("SELECT 1")
            return True
        except Exception:
            self.available = False
            return False

    def get_graph_saver(self) -> BaseCheckpointSaver | None:
        return self._graph_saver if self.available else None

    async def _verify_schema(self) -> None:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(SCHEMA_CHECK_SQL)
            row = await cursor.fetchone()
        if row is None or any(row[name] is None for name in row):
            raise RuntimeError("PostgreSQL checkpoint schema is not initialized")

    async def _cleanup_loop(self) -> None:
        interval = max(60, min(900, self._ttl_seconds // 4))
        while True:
            await asyncio.sleep(interval)
            try:
                await self.cleanup_expired()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.available = False
                logger.warning(
                    "PostgreSQL checkpoint expiration cleanup failed: %s",
                    type(exc).__name__,
                )

    @staticmethod
    def _normalize_state(state: dict[str, Any]) -> dict[str, Any]:
        unknown = set(state) - STATUS_FIELDS
        missing = REQUIRED_STATUS_FIELDS - set(state)
        if unknown:
            raise ValueError(f"Unsupported checkpoint status fields: {sorted(unknown)}")
        if missing:
            raise ValueError(f"Missing checkpoint status fields: {sorted(missing)}")
        return {
            "node": str(state["node"]),
            "requestId": str(state["requestId"]),
            "appId": int(state["appId"]),
            "codeGenType": str(state["codeGenType"]),
            "qualityPassed": state.get("qualityPassed"),
            "repairCount": int(state["repairCount"]),
            "toolCallCount": int(state["toolCallCount"]),
        }
