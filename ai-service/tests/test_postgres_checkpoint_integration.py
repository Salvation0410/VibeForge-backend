from __future__ import annotations

import os
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
    # Match service configuration, including the local .env, without exposing it.
    return Settings().checkpoint_postgres_url


@pytest.mark.asyncio
async def test_real_postgres_checkpoint_lifecycle_and_expiration_cleanup():
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
            },
        )
        async with await AsyncConnection.connect(
            POSTGRES_URL,
            autocommit=True,
            row_factory=dict_row,
        ) as connection:
            row = await (
                await connection.execute(
                    "SELECT node FROM ai_workflow_status WHERE thread_id = %s",
                    (thread_id,),
                )
            ).fetchone()
            assert row == {"node": "input_guard"}
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
