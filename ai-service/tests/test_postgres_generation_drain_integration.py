"""在真实删除事务尚未提交时重复取消；模型与 Spring 工具仍为 Fake。"""

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection

from ai_service.api.schemas import GenerationRequest
from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.workflow import GenerationWorkflow
from conftest import FakeModel, FakeToolGateway
from test_postgres_checkpoint_integration import isolate_expiration_cleanup, postgres_url, pytestmark
from test_stream_terminal_cleanup import ExitingModel, advance_ready_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stop", "disconnect", "success", "failure"])
async def test_postgres_delete_transaction_survives_repeated_cancel(settings, monkeypatch, caplog, mode):
    entered, release, interrupted = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class GatedSaver(AsyncPostgresSaver):
        deleting = False

        async def adelete_thread(self, thread_id):
            self.deleting = True
            try:
                await super().adelete_thread(thread_id)
            finally:
                self.deleting = False

        @asynccontextmanager
        async def _cursor(self, *args, **kwargs):
            async with super()._cursor(*args, **kwargs) as cursor:
                yield cursor
                if self.deleting:
                    # 保留官方删除和事务管理，只在 DELETE 已执行、提交前挂起。
                    entered.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        interrupted.set()
                        raise

    class FailingModel(FakeModel):
        async def generate(self, branch, context):
            raise RuntimeError("TEST_GENERATION_FAILED")

    model = ExitingModel() if mode in {"stop", "disconnect"} else (
        FailingModel() if mode == "failure" else FakeModel())
    request_id = f"postgres-drain-{uuid4().hex}"
    thread_id = f"42:{request_id}"
    checkpoint = PostgresCheckpoint(
        postgres_url(), required=True, auto_setup=True, ttl_seconds=60,
        pool_min_size=1, pool_max_size=2, saver_factory=GatedSaver,
    )
    isolate_expiration_cleanup(checkpoint, [thread_id], monkeypatch)
    await checkpoint.start()
    workflow = GenerationWorkflow(model=model, tool_gateway=FakeToolGateway(), checkpoint=checkpoint,
                                  cancellations=CancellationRegistry(), settings=settings)
    request = GenerationRequest(appId="42", requestId=request_id, prompt="build", codeGenType="HTML")

    async def consume():
        return [event async for event in workflow.stream(request)]

    def stop():
        workflow.cancellations.cancel(thread_id)
        assert workflow.active_generations.cancel(thread_id)

    consumer = asyncio.create_task(consume())
    try:
        if mode in {"stop", "disconnect"}:
            await asyncio.wait_for(model.started.wait(), 10)
            if mode == "stop":
                stop()
            else:
                consumer.cancel()
            await asyncio.wait_for(model.exiting.wait(), 10)
            stop()
            model.release.set()
        await asyncio.wait_for(entered.wait(), 10)
        # 独立连接仍能读到未提交删除前的记录，证明门闩覆盖了真实事务。
        async with await AsyncConnection.connect(postgres_url(), autocommit=True) as connection:
            row = await (await connection.execute(
                "SELECT count(*) FROM checkpoints WHERE thread_id = %s", (thread_id,),
            )).fetchone()
            assert row[0] > 0
        stop()
        stop()
        if mode == "disconnect":
            consumer.cancel()
        await advance_ready_tasks()
        assert not interrupted.is_set()
        assert not consumer.done()
        release.set()
        if mode == "disconnect":
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(consumer, 10)
        else:
            events = await asyncio.wait_for(consumer, 10)
            terminals = [e for e in events if e.type in {"completed", "failed"}]
            assert len(terminals) == 1
            assert terminals[0].type == ("completed" if mode == "success" else "failed")
            if mode != "success":
                assert terminals[0].error.code == ("cancelled" if mode == "stop" else "TEST_GENERATION_FAILED")
        assert not workflow.active_generations.is_active(thread_id)
        assert not workflow.cancellations.is_cancelled(thread_id)
        assert await checkpoint.ping()
        async with await AsyncConnection.connect(postgres_url(), autocommit=True) as connection:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                row = await (await connection.execute(
                    f"SELECT count(*) FROM {table} WHERE thread_id = %s", (thread_id,),
                )).fetchone()
                assert row == (0,), table
        assert not [r for r in caplog.records if r.levelno >= 30 and (
            r.name.startswith("psycopg") or "checkpoint" in r.name)]
    finally:
        release.set()
        if mode in {"stop", "disconnect"}:
            model.release.set()
        if not consumer.done():
            consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        try:
            await checkpoint._graph_saver.adelete_thread(thread_id)
            async with await AsyncConnection.connect(postgres_url(), autocommit=True) as connection:
                await connection.execute("DELETE FROM ai_workflow_status WHERE thread_id = %s", (thread_id,))
        finally:
            await checkpoint.close()
