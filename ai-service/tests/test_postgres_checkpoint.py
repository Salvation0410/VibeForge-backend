from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from ai_service.infrastructure import checkpoint_setup
from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint


class FakeCursor:
    def __init__(self, *, row=None, rows=None):
        self._row = row
        self._rows = list(rows or [])

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self):
        self.queries: list[tuple[str, object]] = []
        self.schema_row = {
            "checkpoints": "checkpoints",
            "checkpoint_blobs": "checkpoint_blobs",
            "checkpoint_writes": "checkpoint_writes",
            "ai_workflow_status": "ai_workflow_status",
        }
        self.expired_rows: list[dict[str, str]] = []

    async def execute(self, query: str, params=None):
        self.queries.append((query, params))
        if "to_regclass" in query:
            return FakeCursor(row=self.schema_row)
        if "SELECT thread_id FROM ai_workflow_status" in query:
            return FakeCursor(rows=self.expired_rows)
        return FakeCursor()


class FakePool:
    def __init__(self, *, open_error: Exception | None = None):
        self.connection_object = FakeConnection()
        self.open_error = open_error
        self.opened = False
        self.closed = False

    async def open(self, *, wait: bool, timeout: float):
        if self.open_error is not None:
            raise self.open_error
        self.opened = True

    async def close(self):
        self.closed = True

    @asynccontextmanager
    async def connection(self):
        yield self.connection_object


class FakeSaver:
    def __init__(self):
        self.setup_calls = 0
        self.deleted: list[str] = []
        self.fail_threads: set[str] = set()

    async def setup(self):
        self.setup_calls += 1

    async def adelete_thread(self, thread_id: str):
        if thread_id in self.fail_threads:
            raise OSError("delete failed")
        self.deleted.append(thread_id)


def checkpoint_with(pool: FakePool, saver: FakeSaver, **overrides) -> PostgresCheckpoint:
    return PostgresCheckpoint(
        "postgresql://ignored",
        required=overrides.get("required", False),
        auto_setup=overrides.get("auto_setup", True),
        ttl_seconds=overrides.get("ttl_seconds", 3600),
        pool_min_size=1,
        pool_max_size=5,
        pool_factory=lambda **_: pool,
        saver_factory=lambda _, **__: saver,
    )


@pytest.mark.asyncio
async def test_start_sets_up_schema_and_exposes_official_saver():
    pool = FakePool()
    saver = FakeSaver()
    checkpoint = checkpoint_with(pool, saver)

    await checkpoint.start()

    assert pool.opened is True
    assert saver.setup_calls == 1
    assert checkpoint.available is True
    assert checkpoint.get_graph_saver() is saver
    assert any("CREATE TABLE IF NOT EXISTS ai_workflow_status" in q for q, _ in pool.connection_object.queries)
    await checkpoint.close()
    assert pool.closed is True


@pytest.mark.asyncio
async def test_start_without_auto_setup_requires_existing_schema():
    pool = FakePool()
    saver = FakeSaver()
    checkpoint = checkpoint_with(pool, saver, auto_setup=False)

    await checkpoint.start()

    assert saver.setup_calls == 0
    assert checkpoint.available is True
    await checkpoint.close()


@pytest.mark.asyncio
async def test_optional_start_failure_degrades_without_raising():
    pool = FakePool(open_error=OSError("unavailable"))
    checkpoint = checkpoint_with(pool, FakeSaver(), required=False)

    await checkpoint.start()

    assert checkpoint.available is False
    assert checkpoint.get_graph_saver() is None
    assert pool.closed is True


@pytest.mark.asyncio
async def test_required_start_failure_raises_stable_error():
    checkpoint = checkpoint_with(
        FakePool(open_error=OSError("unavailable")),
        FakeSaver(),
        required=True,
    )

    with pytest.raises(RuntimeError, match="PostgreSQL checkpoint is required"):
        await checkpoint.start()


@pytest.mark.asyncio
async def test_save_upserts_only_sanitized_status():
    pool = FakePool()
    checkpoint = checkpoint_with(pool, FakeSaver())
    checkpoint.available = True
    state = {
        "node": "quality_review",
        "requestId": "req-1",
        "appId": "42",
        "codeGenType": "HTML",
        "qualityPassed": True,
        "repairCount": 1,
        "toolCallCount": 0,
    }

    await checkpoint.save("42:req-1", state)

    query, params = pool.connection_object.queries[-1]
    assert "ON CONFLICT (thread_id) DO UPDATE" in query
    assert params[0:5] == ("42:req-1", 42, "req-1", "HTML", "quality_review")
    assert params[-1] == 3600


@pytest.mark.asyncio
async def test_save_rejects_source_fields_before_sql():
    pool = FakePool()
    checkpoint = checkpoint_with(pool, FakeSaver())
    checkpoint.available = True

    with pytest.raises(ValueError, match="artifact"):
        await checkpoint.save(
            "42:req-1",
            {
                "node": "quality_review",
                "requestId": "req-1",
                "appId": 42,
                "codeGenType": "HTML",
                "qualityPassed": True,
                "repairCount": 0,
                "toolCallCount": 0,
                "artifact": "secret source",
            },
        )

    assert pool.connection_object.queries == []


@pytest.mark.asyncio
async def test_cleanup_expired_retries_failed_graph_deletion():
    pool = FakePool()
    pool.connection_object.expired_rows = [
        {"thread_id": "42:ok"},
        {"thread_id": "42:retry"},
    ]
    saver = FakeSaver()
    saver.fail_threads.add("42:retry")
    checkpoint = checkpoint_with(pool, saver)
    checkpoint.available = True

    await checkpoint.cleanup_expired()

    assert saver.deleted == ["42:ok"]
    delete_params = [
        params
        for query, params in pool.connection_object.queries
        if "DELETE FROM ai_workflow_status WHERE thread_id" in query
    ]
    assert delete_params == [("42:ok",)]


@pytest.mark.asyncio
async def test_cleanup_graph_failure_degrades_without_raising():
    pool = FakePool()
    saver = FakeSaver()
    saver.fail_threads.add("42:req-1")
    checkpoint = checkpoint_with(pool, saver, required=True)
    checkpoint.available = True

    await checkpoint.cleanup_graph("42:req-1")

    assert checkpoint.available is False


@pytest.mark.asyncio
async def test_setup_command_uses_required_auto_setup(monkeypatch):
    calls = {}

    class SetupCheckpoint:
        def __init__(self, url, **kwargs):
            calls["url"] = url
            calls["kwargs"] = kwargs

        async def start(self):
            calls["started"] = True

        async def close(self):
            calls["closed"] = True

    settings = type(
        "SettingsStub",
        (),
        {
            "checkpoint_postgres_url": "postgresql://checkpoint.test/db",
            "checkpoint_ttl_seconds": 3600,
            "checkpoint_pool_min_size": 1,
            "checkpoint_pool_max_size": 3,
        },
    )()
    monkeypatch.setattr(checkpoint_setup, "get_settings", lambda: settings)
    monkeypatch.setattr(checkpoint_setup, "PostgresCheckpoint", SetupCheckpoint)

    await checkpoint_setup.main_async()

    assert calls == {
        "url": "postgresql://checkpoint.test/db",
        "kwargs": {
            "required": True,
            "auto_setup": True,
            "ttl_seconds": 3600,
            "pool_min_size": 1,
            "pool_max_size": 3,
        },
        "started": True,
        "closed": True,
    }
