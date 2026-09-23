# LangGraph Checkpoint 产物留存收敛 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 业务 checkpoint 不再保存完整源码，并在任一终态后立即清理当前请求的 LangGraph 自动 checkpoint，且清理失败不改变业务终态。

**Architecture:** `CheckpointStore` 提供统一的 `cleanup_graph(thread_id)` 生命周期方法，Redis 实现复用已有 `RedisGraphSaver.adelete_thread()`，禁用实现为空操作。`GenerationWorkflow` 在最外层 `finally` 中调用清理；运行期间仍保留完整 `WorkflowState.artifact`，业务摘要则永久移除该字段。

**Tech Stack:** Python 3.12、FastAPI、LangGraph、redis-py asyncio、pytest、uv。

---

## File Map

- Modify `ai-service/src/ai_service/orchestration/workflow.py`: 移除业务快照中的源码，并在终态后执行不反转业务结果的清理。
- Modify `ai-service/src/ai_service/infrastructure/checkpoint.py`: 扩展 checkpoint 生命周期协议，由 Redis 实现委派现有 graph saver 删除能力。
- Modify `ai-service/tests/conftest.py`: 让内存 checkpoint 记录清理调用，供 API 测试断言。
- Modify `ai-service/tests/test_api.py`: 覆盖无源码摘要、成功/失败/取消/协程取消清理和清理失败终态保护。
- Modify `ai-service/tests/test_gateway_and_config.py`: 覆盖 Redis graph 键删除范围、禁用降级和清理失败行为。
- Modify `ai-service/README.md`: 记录执行期留存、终态清理和 TTL 兜底语义。
- Modify `doc/ai-service-phase-one-handoff.md`: 更新本项完成状态、测试基线和后续优先级。

### Task 1: Remove Source From Business Checkpoints

**Files:**
- Modify: `ai-service/tests/test_api.py`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py:543-554`

- [ ] **Step 1: Write the failing business checkpoint contract assertion**

在 `test_all_generation_branches_complete_in_order` 的 `checkpoint.saved` 断言后增加：

```python
        saved_states = checkpoint.saved["42:req-1"]
        assert saved_states
        assert all("artifact" not in state for state in saved_states)
        assert all("prompt" not in state for state in saved_states)
        assert all("conversation" not in state for state in saved_states)
        assert all("context" not in state for state in saved_states)
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
Set-Location ai-service
uv run pytest tests/test_api.py::test_all_generation_branches_complete_in_order -q
```

Expected: FAIL because at least one saved state contains `artifact`.

- [ ] **Step 3: Remove the source field from the business payload**

在 `_checkpoint_payload()` 中删除这一项，其余摘要字段保持不变：

```python
            "artifact": state.get("artifact"),
```

不要删除 `WorkflowState.artifact`，也不要修改生成、校验、修复、构建或发布节点对运行时产物的使用。保留用户已存在的 `# 工作流的一个状态` 注释。

- [ ] **Step 4: Run the focused test and verify it passes**

Run:

```powershell
uv run pytest tests/test_api.py::test_all_generation_branches_complete_in_order -q
```

Expected: PASS for HTML、MULTI_FILE 和 VUE_PROJECT。

### Task 2: Add Terminal Graph Cleanup Lifecycle

**Files:**
- Modify: `ai-service/src/ai_service/infrastructure/checkpoint.py:20-56,243-301`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py:1-18,122-158`
- Modify: `ai-service/tests/conftest.py:83-100`
- Modify: `ai-service/tests/test_api.py`

- [ ] **Step 1: Make the in-memory checkpoint observable and add failing terminal tests**

将 `MemoryCheckpoint` 扩展为：

```python
class MemoryCheckpoint:
    available = True

    def __init__(self):
        self.saved: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.cleaned_graph_threads: list[str] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def save(self, thread_id: str, state: dict[str, Any]) -> None:
        self.saved[thread_id].append(dict(state))

    async def cleanup_graph(self, thread_id: str) -> None:
        """记录自动 checkpoint 清理请求，不删除测试需要检查的业务摘要。"""
        self.cleaned_graph_threads.append(thread_id)

    async def ping(self) -> bool:
        return self.available
```

在三分支成功测试中增加：

```python
        assert checkpoint.cleaned_graph_threads == ["42:req-1"]
```

修改取消测试以显式注入 checkpoint，并增加断言：

```python
    checkpoint = MemoryCheckpoint()
    app = app_factory(checkpoint=checkpoint)
    # 保留现有请求和终态断言。
    assert checkpoint.cleaned_graph_threads == ["42:req-cancel"]
```

新增失败清理测试：

```python
def test_failed_generation_cleans_graph_checkpoint(app_factory, auth_headers, ndjson_parser):
    class FailingModel(FakeModel):
        async def generate(self, branch, context):
            raise RuntimeError("model unavailable")

    checkpoint = MemoryCheckpoint()
    events = ndjson_parser(TestClient(app_factory(
        model=FailingModel(), checkpoint=checkpoint
    )).post(
        "/internal/v1/generations:stream",
        json=generation_payload("HTML"),
        headers=auth_headers,
    ))

    assert events[-1]["type"] == "failed"
    assert checkpoint.cleaned_graph_threads == ["42:req-1"]
```

- [ ] **Step 2: Run terminal cleanup tests and verify they fail**

Run:

```powershell
uv run pytest tests/test_api.py::test_all_generation_branches_complete_in_order tests/test_api.py::test_failed_generation_cleans_graph_checkpoint tests/test_api.py::test_cancelled_generation_has_explicit_terminal_event -q
```

Expected: FAIL because the workflow does not call `cleanup_graph` yet.

- [ ] **Step 3: Extend the checkpoint protocol and implementations**

在 `CheckpointStore` 中加入：

```python
    async def cleanup_graph(self, thread_id: str) -> None: ...
```

在 `DisabledCheckpoint` 中加入：

```python
    async def cleanup_graph(self, thread_id: str) -> None:
        """禁用 checkpoint 时无需清理持久化状态。"""
        return None
```

在 `RedisCheckpoint` 中加入：

```python
    async def cleanup_graph(self, thread_id: str) -> None:
        """清理终态请求的 LangGraph 数据；失败只影响后续就绪状态。"""
        if not self.available:
            return
        try:
            await self._graph_saver.adelete_thread(thread_id)
        except Exception as exc:
            self.available = False
            logger.warning("LangGraph checkpoint cleanup failed for %s: %s", thread_id, exc)
```

清理失败不得依据 `_required` 重新抛出，因为调用发生在业务终态已经确定之后。

- [ ] **Step 4: Call cleanup defensively from the workflow finally block**

在 `workflow.py` 增加标准日志对象：

```python
import logging

logger = logging.getLogger(__name__)
```

将 `_execute()` 的 `finally` 调整为：

```python
        finally:
            # 清理属于终态后的维护动作，失败不得覆盖已经发送的业务终态。
            try:
                await self.checkpoint.cleanup_graph(thread_id)
            except Exception as exc:
                logger.warning("Checkpoint cleanup failed for %s: %s", thread_id, exc)
            # 请求结束后释放取消标记，避免唯一 requestId 长期累积。
            self.cancellations.clear(thread_id)
```

- [ ] **Step 5: Verify success, failure and explicit cancellation cleanup**

Run:

```powershell
uv run pytest tests/test_api.py::test_all_generation_branches_complete_in_order tests/test_api.py::test_failed_generation_cleans_graph_checkpoint tests/test_api.py::test_cancelled_generation_has_explicit_terminal_event -q
```

Expected: all selected tests PASS and each request records exactly one cleanup。

- [ ] **Step 6: Add cleanup failure and coroutine cancellation tests**

在 `test_api.py` 顶部增加 `asyncio`、`CodeGenType`、`GenerationRequest` 和 `GenerationWorkflow` 导入，然后新增：

```python
@pytest.mark.asyncio
async def test_cleanup_failure_does_not_reverse_completed(settings, caplog):
    class CleanupFailCheckpoint(MemoryCheckpoint):
        async def cleanup_graph(self, thread_id: str) -> None:
            raise RuntimeError("cleanup unavailable")

    workflow = GenerationWorkflow(
        model=FakeModel(),
        tool_gateway=FakeToolGateway(),
        checkpoint=CleanupFailCheckpoint(),
        cancellations=CancellationRegistry(),
        settings=settings,
    )
    events = await workflow.run(GenerationRequest(
        requestId="req-1",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.HTML,
    ))

    assert events[-1].type == "completed"
    assert not [event for event in events if event.type == "failed"]
    assert "Checkpoint cleanup failed for 42:req-1" in caplog.text


@pytest.mark.asyncio
async def test_task_cancellation_cleans_graph_checkpoint(settings):
    started = asyncio.Event()
    never_finish = asyncio.Event()

    class BlockingModel(FakeModel):
        async def generate(self, branch, context):
            started.set()
            await never_finish.wait()
            return await super().generate(branch, context)

    checkpoint = MemoryCheckpoint()
    workflow = GenerationWorkflow(
        model=BlockingModel(),
        tool_gateway=FakeToolGateway(),
        checkpoint=checkpoint,
        cancellations=CancellationRegistry(),
        settings=settings,
    )
    task = asyncio.create_task(workflow.run(GenerationRequest(
        requestId="req-task-cancel",
        appId="42",
        prompt="build it",
        codeGenType=CodeGenType.HTML,
    )))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert checkpoint.cleaned_graph_threads == ["42:req-task-cancel"]
```

同时导入：

```python
from ai_service.api.schemas import CodeGenType, GenerationRequest
from ai_service.orchestration.cancellation import CancellationRegistry
from ai_service.orchestration.workflow import GenerationWorkflow, _after_build
```

- [ ] **Step 7: Run the lifecycle test group**

Run:

```powershell
uv run pytest tests/test_api.py -q
```

Expected: complete API suite PASS；运行中产物校验、修复和发布相关既有测试继续通过，证明 `WorkflowState.artifact` 未被提前删除。

### Task 3: Lock Redis Cleanup Boundaries

**Files:**
- Modify: `ai-service/tests/test_gateway_and_config.py`
- Verify: `ai-service/src/ai_service/infrastructure/checkpoint.py`

- [ ] **Step 1: Add a fake Redis deletion boundary test**

在 `test_gateway_and_config.py` 导入 `fnmatch` 和 `RedisGraphSaver`，新增：

```python
@pytest.mark.asyncio
async def test_graph_saver_deletes_all_keys_for_only_the_target_thread():
    class FakeRedis:
        def __init__(self):
            self.keys = {
                "yu-ai:langgraph:checkpoint:NDI6cmVxLTE:_:cp-1",
                "yu-ai:langgraph:latest:NDI6cmVxLTE:_",
                "yu-ai:langgraph:writes:NDI6cmVxLTE:_:cp-1:task-0",
                "yu-ai:langgraph:checkpoint:NDI6b3RoZXI:_:cp-2",
            }
            self.deleted = []

        async def scan_iter(self, *, match):
            for key in sorted(self.keys):
                if fnmatch.fnmatch(key, match):
                    yield key

        async def delete(self, *keys):
            self.deleted.extend(keys)

    client = FakeRedis()
    saver = RedisGraphSaver(client, ttl_seconds=60)
    await saver.adelete_thread("42:req-1")

    assert set(client.deleted) == {
        "yu-ai:langgraph:checkpoint:NDI6cmVxLTE:_:cp-1",
        "yu-ai:langgraph:latest:NDI6cmVxLTE:_",
        "yu-ai:langgraph:writes:NDI6cmVxLTE:_:cp-1:task-0",
    }
```

该测试是对已有 `adelete_thread()` 的特征锁定，应在生产改动前通过。

- [ ] **Step 2: Cover RedisCheckpoint delegation and degradation**

新增：

```python
@pytest.mark.asyncio
async def test_redis_checkpoint_cleanup_delegates_to_graph_saver(monkeypatch):
    checkpoint = RedisCheckpoint("redis://localhost:6379/0", required=False, ttl_seconds=60)
    checkpoint.available = True
    cleaned = []

    async def record_cleanup(thread_id):
        cleaned.append(thread_id)

    monkeypatch.setattr(checkpoint._graph_saver, "adelete_thread", record_cleanup)
    await checkpoint.cleanup_graph("42:req-1")

    assert cleaned == ["42:req-1"]
    assert checkpoint.available is True


@pytest.mark.asyncio
async def test_redis_checkpoint_cleanup_failure_only_marks_store_unavailable(monkeypatch, caplog):
    checkpoint = RedisCheckpoint("redis://localhost:6379/0", required=True, ttl_seconds=60)
    checkpoint.available = True

    async def fail_cleanup(thread_id):
        raise OSError("redis unavailable")

    monkeypatch.setattr(checkpoint._graph_saver, "adelete_thread", fail_cleanup)
    await checkpoint.cleanup_graph("42:req-1")

    assert checkpoint.available is False
    assert "LangGraph checkpoint cleanup failed for 42:req-1" in caplog.text
```

- [ ] **Step 3: Run checkpoint infrastructure tests**

Run:

```powershell
uv run pytest tests/test_gateway_and_config.py -q
```

Expected: all tests PASS；目标 thread 的三类 graph 键被删除，其他 thread 不受影响，required 模式清理失败也不抛出。

### Task 4: Documentation, Full Verification and Commit

**Files:**
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-phase-one-handoff.md`
- Verify: all task files

- [ ] **Step 1: Document the retention lifecycle**

在 `ai-service/README.md` 的 checkpoint 说明附近加入：

```markdown
业务 checkpoint 只保存节点、生成类型、质量和计数摘要，不保存完整源码。
LangGraph 自动 checkpoint 在请求执行期间仍可能包含完整产物，以支持节点恢复；
请求成功、失败或取消进入终态后会立即清理对应 thread，配置的 TTL 作为异常退出时的兜底回收机制。
终态后的清理失败只会使 checkpoint 就绪状态降级，不会反转已经确定的生成结果。
```

在 `doc/ai-service-phase-one-handoff.md` 中：

- 将 P1“瘦身 checkpoint 中的完整 artifact”标记为已完成，并记录执行期保留、终态清理的准确边界。
- 更新 Python 测试数量和本轮验证命令，以实际运行结果为准。
- 从“下一轮待办”中移除本项，将下一项未完成优化提升为后续首项。
- 保留真实 Redis、真实模型和端到端请求为人工验证项，不写成已经通过。

- [ ] **Step 2: Run complete Python verification**

Run:

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
Set-Location ..
git diff --check
```

Expected: compileall exits 0；完整 pytest 无失败；lock 文件一致；无空白错误。第三方弃用警告可以保留，但必须在交付说明中记录实际数量。

- [ ] **Step 3: Inspect the exact task diff and protect existing user changes**

Run:

```powershell
git status --short
git diff -- ai-service/src/ai_service/orchestration/workflow.py
git diff -- ai-service/src/ai_service/infrastructure/checkpoint.py ai-service/tests/conftest.py ai-service/tests/test_api.py ai-service/tests/test_gateway_and_config.py ai-service/README.md doc/ai-service-phase-one-handoff.md
```

Expected: `.gitignore`、未跟踪的旧计划、`projects/` 和 `workflow.py` 中用户已有的 `# 工作流的一个状态` 注释不进入本任务暂存区。

- [ ] **Step 4: Stage only task-owned hunks**

普通任务文件可直接暂存：

```powershell
git add -- ai-service/src/ai_service/infrastructure/checkpoint.py ai-service/tests/conftest.py ai-service/tests/test_api.py ai-service/tests/test_gateway_and_config.py ai-service/README.md doc/ai-service-phase-one-handoff.md docs/superpowers/plans/2026-09-23-checkpoint-artifact-retention.md
```

`workflow.py` 同时含用户既有修改，必须生成只包含本任务 import、`finally` 和 `_checkpoint_payload` 改动的补丁，再使用 `git apply --cached` 暂存；不得暂存文件顶部用户已有注释。随后运行：

```powershell
git diff --cached --check
git diff --cached -- ai-service/src/ai_service/orchestration/workflow.py
git diff --cached --stat
```

Expected: cached workflow diff 不包含 `# 工作流的一个状态`，且暂存区只包含本任务文件和改动。

- [ ] **Step 5: Commit the optimization**

```powershell
git commit -m "refactor: 收敛 LangGraph checkpoint 产物留存"
git status --short --branch
```

Expected: commit succeeds；用户原有修改和未跟踪内容仍留在工作区，未被提交；不推送远端。
