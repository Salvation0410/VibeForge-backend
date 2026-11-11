from __future__ import annotations

import os
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint
from ai_service.config import Settings

pytestmark = pytest.mark.skipif(
    os.getenv("AI_SERVICE_POSTGRES_INTEGRATION") != "true",
    reason="set AI_SERVICE_POSTGRES_INTEGRATION=true to use real PostgreSQL",
)

def postgres_url() -> str:
    # 使用与服务相同的配置来源（包括本地 .env），但不暴露其中的凭据。
    return Settings().checkpoint_postgres_url


def isolate_expiration_cleanup(checkpoint, thread_ids, monkeypatch):
    """保留生产过期清理逻辑，仅把候选查询限制到本轮 UUID，避免触及同库业务数据。"""
    original_connection = checkpoint._pool.connection

    class ScopedConnection:
        def __init__(self, connection):
            self.connection = connection

        def __getattr__(self, name):
            return getattr(self.connection, name)

        async def execute(self, query, params=None, **kwargs):
            if query == "SELECT thread_id FROM ai_workflow_status WHERE expires_at < now()":
                query += " AND thread_id = ANY(%s)"
                params = (thread_ids,)
            return await self.connection.execute(query, params, **kwargs)

    @asynccontextmanager
    async def connection(*args, **kwargs):
        async with original_connection(*args, **kwargs) as current:
            yield ScopedConnection(current)

    monkeypatch.setattr(checkpoint._pool, "connection", connection)


@pytest.mark.asyncio
async def test_real_postgres_checkpoint_lifecycle_and_expiration_cleanup(monkeypatch):
    POSTGRES_URL = postgres_url()
    thread_id = f"integration:{uuid4().hex}"
    checkpoint = PostgresCheckpoint(
        POSTGRES_URL,
        required=True,
        auto_setup=True,
        ttl_seconds=60,
        pool_min_size=1,
        pool_max_size=2,
    )
    isolate_expiration_cleanup(checkpoint, [thread_id], monkeypatch)
    await checkpoint.start()
    saver = checkpoint.get_graph_saver()
    assert saver is not None
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    checkpoint_id = uuid4().hex
    graph_state = {
        "v": 4,
        "ts": datetime.now(UTC).isoformat(),
        "id": checkpoint_id,
        "channel_values": {"node": "input_guard"},
        "channel_versions": {"node": "1"},
        "versions_seen": {},
    }

    try:
        next_config = await saver.aput(config, graph_state, {}, {"node": "1"})
        await saver.aput_writes(next_config, [("node", "pending")], "task-1")
        stored = await saver.aget_tuple(config)
        assert stored is not None
        assert stored.checkpoint["channel_values"]["node"] == "input_guard"
        assert [item async for item in saver.alist(config, limit=1)]

        await checkpoint.save(
            thread_id,
            {
                "node": "input_guard",
                "requestId": thread_id.split(":", 1)[1],
                "appId": 42,
                "codeGenType": "HTML",
                "qualityPassed": None,
                "repairCount": 0,
                "toolCallCount": 0,
                "preparation_diagnostics": {
                    "rewriteStatus": "ready", "reason": "NONE", "durationMs": 12.5,
                    "modelAttempted": True, "briefBytes": 128,
                    "skillIds": ["design-core@1", "private-skill"],
                    "prompt": "private-prompt", "source": "private-source",
                    "brief": "private-brief", "token": "private-token",
                },
            },
        )
        async with await AsyncConnection.connect(
            POSTGRES_URL,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            row = await (
                await connection.execute(
                    "SELECT node, status_payload FROM ai_workflow_status WHERE thread_id = %s",
                    (thread_id,),
                )
            ).fetchone()
            assert row["node"] == "input_guard"
            # 独立连接核对实际 JSONB，确保新增观测能持久化且不泄漏自由文本。
            assert row["status_payload"]["preparation_diagnostics"] == {
                "rewriteStatus": "ready", "reason": "NONE", "durationMs": 12.5,
                "modelAttempted": True, "briefBytes": 128, "skillIds": ["design-core@1"],
            }
            assert "private-" not in json.dumps(row["status_payload"])
            await connection.execute(
                "UPDATE ai_workflow_status SET expires_at = now() - interval '1 second' "
                "WHERE thread_id = %s",
                (thread_id,),
            )

        await checkpoint.cleanup_expired()
        assert await saver.aget_tuple(config) is None
        async with await AsyncConnection.connect(
            POSTGRES_URL,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            row = await (
                await connection.execute(
                    "SELECT thread_id FROM ai_workflow_status WHERE thread_id = %s",
                    (thread_id,),
                )
            ).fetchone()
            assert row is None
    finally:
        await saver.adelete_thread(thread_id)
        async with await AsyncConnection.connect(
            POSTGRES_URL,
            autocommit=True,
        ) as connection:
            await connection.execute(
                "DELETE FROM ai_workflow_status WHERE thread_id = %s",
                (thread_id,),
            )
        await checkpoint.close()


@pytest.mark.asyncio
async def test_real_postgres_graph_failure_and_cancel_leave_pool_usable(monkeypatch):
    import asyncio
    from typing import TypedDict
    from langgraph.graph import StateGraph, START, END

    class State(TypedDict):
        mode: str

    checkpoint = PostgresCheckpoint(
        postgres_url(), required=True, auto_setup=True, ttl_seconds=60,
        pool_min_size=1, pool_max_size=2,
    )
    threads = [f"integration:{uuid4().hex}" for _ in range(2)]
    isolate_expiration_cleanup(checkpoint, threads, monkeypatch)
    await checkpoint.start()
    saver = checkpoint.get_graph_saver()
    entered = asyncio.Event()

    async def node(state):
        if state["mode"] == "fail":
            raise ValueError("simulated model truncation")
        entered.set()
        await asyncio.Event().wait()
        return {}

    builder = StateGraph(State)
    builder.add_node("generate", node)
    builder.add_edge(START, "generate")
    builder.add_edge("generate", END)
    graph = builder.compile(checkpointer=saver)
    task = None
    try:
        with pytest.raises(ValueError, match="simulated model truncation"):
            await graph.ainvoke({"mode": "fail"}, {"configurable": {"thread_id": threads[0]}})
        await checkpoint.cleanup_graph(threads[0])
        assert await checkpoint.ping()
        task = asyncio.create_task(graph.ainvoke(
            {"mode": "cancel"}, {"configurable": {"thread_id": threads[1]}},
        ))
        await asyncio.wait_for(entered.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await checkpoint.cleanup_graph(threads[1])
        assert await checkpoint.ping()
        assert await saver.aget_tuple({"configurable": {"thread_id": threads[1]}}) is None
    finally:
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        for thread in threads:
            await saver.adelete_thread(thread)
        await checkpoint.close()
