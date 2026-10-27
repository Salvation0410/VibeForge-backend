"""真实 PostgreSQL 子图验收；仅验证底层恢复，不开放业务恢复入口。"""

from uuid import uuid4

import pytest
from psycopg import AsyncConnection

from ai_service.orchestration.events import EventEmitter
from ai_service.orchestration.subgraphs.vue import vue_recursion_limit
from conftest import FakeModel
from test_postgres_checkpoint_integration import postgres_url, pytestmark
from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint
from test_subgraphs import request_for
from test_vue_subgraph import BatchModel, initial, make_workflow, paused_vue_state


def new_checkpoint():
    return PostgresCheckpoint(
        postgres_url(), required=True, auto_setup=True, ttl_seconds=60,
        pool_min_size=1, pool_max_size=2,
    )


async def assert_storage_empty(thread_id):
    # 同时检查物理表，避免只验证根 checkpoint 而漏掉子命名空间和待写入记录。
    async with await AsyncConnection.connect(postgres_url(), autocommit=True) as connection:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            count = await (await connection.execute(
                f"SELECT count(*) FROM {table} WHERE thread_id = %s", (thread_id,),
            )).fetchone()
            assert count == (0,), table


async def remove_test_thread(checkpoint, thread_id):
    # 只删除本测试 UUID 对应的记录，不修改其他线程或数据库配置。
    await checkpoint.get_graph_saver().adelete_thread(thread_id)
    async with await AsyncConnection.connect(postgres_url(), autocommit=True) as connection:
        await connection.execute("DELETE FROM ai_workflow_status WHERE thread_id = %s", (thread_id,))


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", [False, True])
@pytest.mark.parametrize("boundary", ["model_turn", "execute_tool"])
async def test_postgres_reconnect_resumes_nested_vue_batch(settings, monkeypatch, repair, boundary):
    import ai_service.orchestration.workflow as workflow_module

    original = workflow_module.build_vue_tool_subgraph

    def interrupted(*args, **kwargs):
        if (kwargs.get("node") == "repair") == repair:
            kwargs["interrupt_after"] = [boundary]
        return original(*args, **kwargs)

    request_id = f"postgres-subgraph-{uuid4().hex}"
    thread_id = f"42:{request_id}"
    config = {"configurable": {"thread_id": thread_id},
              "recursion_limit": vue_recursion_limit(settings)}
    checkpoint = new_checkpoint()
    await checkpoint.start()
    model = BatchModel(reviews=[False, True] if repair else [True])
    workflow = make_workflow(settings, model, checkpoint)
    emitter = EventEmitter(request_id)
    try:
        monkeypatch.setattr(workflow_module, "build_vue_tool_subgraph", interrupted)
        graph = workflow._build_graph(emitter, thread_id, {})
        state = {**initial(), "request_id": request_id, "thread_id": thread_id}
        await graph.ainvoke(state, config)
        snapshot = await paused_vue_state(graph, config, repair)
        assert snapshot.values["vue_loop"]["ordinal"] == (boundary == "execute_tool")
        assert "vue_loop" not in (await graph.aget_state(config)).values
        before_calls = len(model.calls)
        before_tools = list(workflow.tool_gateway.calls)
        namespaces = {entry.config["configurable"]["checkpoint_ns"]
                      async for entry in checkpoint.get_graph_saver().alist(config)}
        assert "" in namespaces
        assert any("vue_repair:" in ns if repair else "vue_generation:" in ns for ns in namespaces)

        # 关闭原连接池并重建 workflow/saver，恢复不能依赖原图对象或连接内缓存。
        await checkpoint.close()
        checkpoint = new_checkpoint()
        await checkpoint.start()
        monkeypatch.setattr(workflow_module, "build_vue_tool_subgraph", original)
        rebuilt_workflow = make_workflow(settings, model, checkpoint, gateway=workflow.tool_gateway)
        rebuilt = rebuilt_workflow._build_graph(emitter, thread_id, {})
        result = await rebuilt.ainvoke(None, config)
        assert result["tool_call_count"] == (4 if repair else 2)
        assert result["repair_count"] == int(repair)
        assert result["token_usage"] == {"output_tokens": 5}
        assert "vue_loop" not in result
        assert "vue_tool_results" not in result
        phase = "repair" if repair else "generate"
        assert sum(name == phase for name, _ in model.calls[before_calls:]) == 1
        assert rebuilt_workflow.tool_gateway.calls[:len(before_tools)] == before_tools
        prefix = f"{request_id}:vue-repair:1" if repair else f"{request_id}:vue-generate"
        tool_ids = [call["toolCallId"] for call in rebuilt_workflow.tool_gateway.calls
                    if call["toolCallId"].startswith(prefix)]
        assert tool_ids == [f"{prefix}:1", f"{prefix}:2"]
        assert sum(event.type == "completed" for event in emitter.events) == 1
        await checkpoint.cleanup_graph(thread_id)
        assert checkpoint.available
        await assert_storage_empty(thread_id)
    finally:
        try:
            await remove_test_thread(checkpoint, thread_id)
        finally:
            await checkpoint.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "failed", "cancelled"])
async def test_postgres_terminal_cleans_all_subgraph_storage(settings, outcome):
    checkpoint = new_checkpoint()
    await checkpoint.start()
    request = request_for("VUE_PROJECT").model_copy(update={"request_id": f"postgres-terminal-{uuid4().hex}"})
    thread_id = f"42:{request.request_id}"
    workflow = make_workflow(settings, FakeModel(reviews=[False] * 3 if outcome == "failed" else [True]), checkpoint)
    namespaces = set()
    cleanup = checkpoint.cleanup_graph

    async def observed_cleanup(thread):
        config = {"configurable": {"thread_id": thread}}
        namespaces.update({entry.config["configurable"]["checkpoint_ns"]
                           async for entry in checkpoint.get_graph_saver().alist(config)})
        await cleanup(thread)

    checkpoint.cleanup_graph = observed_cleanup
    if outcome == "cancelled":
        class CancellingModel(FakeModel):
            async def generate(self, branch, context):
                turn = await super().generate(branch, context)
                workflow.cancellations.cancel(thread_id)
                return turn
        workflow.model = CancellingModel()
    try:
        events = await workflow.run(request)
        assert events[-1].type == ("completed" if outcome == "success" else "failed")
        if outcome == "cancelled":
            assert events[-1].error.code == "cancelled"
        assert "" in namespaces
        assert any(ns.startswith("vue_generation:") for ns in namespaces)
        assert checkpoint.available
        await assert_storage_empty(thread_id)
        assert await checkpoint.ping()
    finally:
        try:
            await remove_test_thread(checkpoint, thread_id)
        finally:
            await checkpoint.close()


@pytest.mark.asyncio
async def test_postgres_expiration_cleans_nested_storage_and_preserves_active_thread(settings, monkeypatch):
    import ai_service.orchestration.workflow as workflow_module

    original = workflow_module.build_vue_tool_subgraph

    def interrupted(*args, **kwargs):
        kwargs["interrupt_after"] = ["execute_tool"]
        return original(*args, **kwargs)

    monkeypatch.setattr(workflow_module, "build_vue_tool_subgraph", interrupted)
    checkpoint = new_checkpoint()
    await checkpoint.start()
    threads = [f"42:postgres-ttl-{uuid4().hex}" for _ in range(2)]
    try:
        for thread_id in threads:
            request_id = thread_id.split(":", 1)[1]
            workflow = make_workflow(settings, BatchModel(), checkpoint)
            graph = workflow._build_graph(EventEmitter(request_id), thread_id, {})
            config = {"configurable": {"thread_id": thread_id},
                      "recursion_limit": vue_recursion_limit(settings)}
            await graph.ainvoke({**initial(), "thread_id": thread_id, "request_id": request_id}, config)
            namespaces = {entry.config["configurable"]["checkpoint_ns"]
                          async for entry in checkpoint.get_graph_saver().alist(config)}
            assert "" in namespaces
            assert any(ns.startswith("vue_generation:") for ns in namespaces)

        async with await AsyncConnection.connect(postgres_url(), autocommit=True) as connection:
            await connection.execute(
                "UPDATE ai_workflow_status SET expires_at = now() - interval '1 second' WHERE thread_id = %s",
                (threads[0],),
            )
        await checkpoint.cleanup_expired()
        await assert_storage_empty(threads[0])
        saver = checkpoint.get_graph_saver()
        assert await saver.aget_tuple({"configurable": {"thread_id": threads[0]}}) is None
        active = [entry async for entry in saver.alist({"configurable": {"thread_id": threads[1]}})]
        assert any(entry.config["configurable"]["checkpoint_ns"] for entry in active)
        async with await AsyncConnection.connect(postgres_url(), autocommit=True) as connection:
            rows = await (await connection.execute(
                "SELECT thread_id FROM ai_workflow_status WHERE thread_id = ANY(%s)", (threads,),
            )).fetchall()
            assert rows == [(threads[1],)]
        assert await checkpoint.ping()
    finally:
        try:
            for thread_id in threads:
                await remove_test_thread(checkpoint, thread_id)
        finally:
            await checkpoint.close()
