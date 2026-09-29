# PostgreSQL Checkpoint Runtime Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the PostgreSQL checkpoint lifecycle safe with a one-connection pool, expand offline and opt-in real-database coverage, and provide a secret-safe runtime verification gate.

**Architecture:** Refactor expiration cleanup into query, official graph deletion, and status deletion phases so the saver never requests a nested connection while the wrapper holds the only pool connection. Keep official checkpoint SQL inside `AsyncPostgresSaver`; add focused integration tests and a dedicated dry-run-first PowerShell gate before wiring it into the central verification script.

**Tech Stack:** Python 3.12, FastAPI, LangGraph 0.6.x, `langgraph-checkpoint-postgres` 3.0.x, Psycopg 3 async pool, PostgreSQL 16 in the user's existing Docker environment, pytest, uv, Windows PowerShell 5.1, PowerShell 7.

---

## Multi-agent execution model

First wave may run in parallel because file ownership is disjoint:

```text
Agent A -> Task 1: Python implementation and offline tests
Agent B -> Task 2: new opt-in real PostgreSQL integration module
Agent C -> Task 3: dedicated PowerShell runtime gate and its tests
```

After all three pass package-level review, execute sequentially:

```text
Task 4 -> central verification integration
Task 5 -> documentation and handoff
Task 6 -> full offline verification
Task 7 -> user-assisted real PostgreSQL execution
```

Agent A is the only writer for `postgres_checkpoint.py` and `test_postgres_checkpoint.py`. Agent B must create a new integration file rather than editing Agent A's tests. Agent C must not edit the central verification scripts until Task 4.

## File map

- Modify `ai-service/src/ai_service/infrastructure/postgres_checkpoint.py`: release the query connection before official graph deletion and verify the full official schema.
- Modify `ai-service/tests/test_postgres_checkpoint.py`: add one-connection cleanup, schema, ping, write failure, close, and cleanup delegation coverage.
- Create `ai-service/tests/test_postgres_checkpoint_runtime_integration.py`: opt-in real setup, single-pool, upsert, terminal, and expiration coverage.
- Create `scripts/verify-postgres-checkpoint-runtime.ps1`: dedicated dry-run-first PostgreSQL verification entrypoint.
- Create `scripts/postgres-checkpoint-validation.tests.ps1`: static and behavior checks for the dedicated entrypoint.
- Modify `scripts/verify-langgraph-real-gate.ps1`: invoke the dedicated gate for `-IncludePostgres` instead of duplicating its test command.
- Modify `scripts/ai-validation-scripts.tests.ps1`: require and validate the dedicated scripts.
- Modify `ai-service/README.md`, `doc/ai-service-startup.md`, `doc/ai-service-phase-one-handoff.md`, and `AGENTS.md`: document final runtime behavior and evidence boundaries.

## Task 1: Make lifecycle and cleanup safe with one pool connection

**Owner:** Agent A

**Files:**
- Modify: `ai-service/src/ai_service/infrastructure/postgres_checkpoint.py`
- Modify: `ai-service/tests/test_postgres_checkpoint.py`

- [ ] **Step 1: Add a failing nested-connection regression test**

Extend the unit-test fakes with a pool that records active leases:

```python
class ExclusiveFakePool(FakePool):
    def __init__(self, *, open_error: Exception | None = None):
        super().__init__(open_error=open_error)
        self.active_connections = 0

    @asynccontextmanager
    async def connection(self):
        if self.active_connections:
            raise AssertionError("nested pool connection")
        self.active_connections += 1
        try:
            yield self.connection_object
        finally:
            self.active_connections -= 1


class PoolAwareSaver(FakeSaver):
    def __init__(self, pool: ExclusiveFakePool):
        super().__init__()
        self.pool = pool

    async def adelete_thread(self, thread_id: str):
        assert self.pool.active_connections == 0
        await super().adelete_thread(thread_id)
```

Add:

```python
@pytest.mark.asyncio
async def test_cleanup_expired_releases_query_connection_before_graph_delete():
    pool = ExclusiveFakePool()
    pool.connection_object.expired_rows = [{"thread_id": "42:expired"}]
    saver = PoolAwareSaver(pool)
    checkpoint = checkpoint_with(pool, saver)
    checkpoint.available = True

    await checkpoint.cleanup_expired()

    assert saver.deleted == ["42:expired"]
    assert pool.active_connections == 0
    assert any(
        "DELETE FROM ai_workflow_status WHERE thread_id" in query
        for query, _ in pool.connection_object.queries
    )
```

- [ ] **Step 2: Run the regression test and confirm it fails**

Run:

```powershell
cd ai-service
uv run pytest tests/test_postgres_checkpoint.py::test_cleanup_expired_releases_query_connection_before_graph_delete -q
```

Expected: FAIL with `AssertionError: nested pool connection` because `cleanup_expired()` still holds the query connection while calling the saver.

- [ ] **Step 3: Refactor expiration cleanup into separate leases**

Replace the method body with the following shape:

```python
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
        except Exception as exc:
            logger.warning(
                "Expired PostgreSQL checkpoint cleanup failed for %s: %s",
                thread_id,
                type(exc).__name__,
            )
            continue

        try:
            async with self._pool.connection() as connection:
                await connection.execute(
                    "DELETE FROM ai_workflow_status WHERE thread_id = %s",
                    (thread_id,),
                )
        except Exception as exc:
            logger.warning(
                "Expired PostgreSQL status cleanup failed for %s: %s",
                thread_id,
                type(exc).__name__,
            )
```

Do not copy official checkpoint deletion SQL. Do not delete the status row when `adelete_thread()` fails.

- [ ] **Step 4: Add full schema verification**

Add the official migration table to `SCHEMA_CHECK_SQL`:

```sql
SELECT
    to_regclass('public.checkpoint_migrations') AS checkpoint_migrations,
    to_regclass('public.checkpoints') AS checkpoints,
    to_regclass('public.checkpoint_blobs') AS checkpoint_blobs,
    to_regclass('public.checkpoint_writes') AS checkpoint_writes,
    to_regclass('public.ai_workflow_status') AS ai_workflow_status
```

Update `FakeConnection.schema_row` to include `checkpoint_migrations`, then add optional and required missing-schema tests:

```python
@pytest.mark.asyncio
async def test_optional_missing_schema_degrades_and_closes_pool():
    pool = FakePool()
    pool.connection_object.schema_row["checkpoint_migrations"] = None
    checkpoint = checkpoint_with(pool, FakeSaver(), auto_setup=False, required=False)

    await checkpoint.start()

    assert checkpoint.available is False
    assert pool.closed is True


@pytest.mark.asyncio
async def test_required_missing_schema_raises_stable_error():
    pool = FakePool()
    pool.connection_object.schema_row["checkpoint_migrations"] = None
    checkpoint = checkpoint_with(pool, FakeSaver(), auto_setup=False, required=True)

    with pytest.raises(RuntimeError, match="PostgreSQL checkpoint is required"):
        await checkpoint.start()
```

- [ ] **Step 5: Add lifecycle failure and delegation tests**

Enhance `FakeConnection` with configurable execute failures:

```python
class FakeConnection:
    def __init__(self):
        # keep the existing fields
        self.fail_markers: set[str] = set()

    async def execute(self, query: str, params=None):
        self.queries.append((query, params))
        if any(marker in query for marker in self.fail_markers):
            raise OSError("database unavailable")
        # keep the existing cursor behavior below
```

Add complete tests:

```python
@pytest.mark.asyncio
async def test_ping_failure_marks_checkpoint_unavailable():
    pool = FakePool()
    pool.connection_object.fail_markers.add("SELECT 1")
    checkpoint = checkpoint_with(pool, FakeSaver())
    checkpoint.available = True

    assert await checkpoint.ping() is False
    assert checkpoint.available is False

@pytest.mark.asyncio
async def test_optional_save_failure_degrades_without_raising():
    pool = FakePool()
    pool.connection_object.fail_markers.add("INSERT INTO ai_workflow_status")
    checkpoint = checkpoint_with(pool, FakeSaver(), required=False)
    checkpoint.available = True

    await checkpoint.save("42:req", valid_status())

    assert checkpoint.available is False

@pytest.mark.asyncio
async def test_required_save_failure_raises_stable_error():
    pool = FakePool()
    pool.connection_object.fail_markers.add("INSERT INTO ai_workflow_status")
    checkpoint = checkpoint_with(pool, FakeSaver(), required=True)
    checkpoint.available = True

    with pytest.raises(RuntimeError, match="PostgreSQL checkpoint write failed"):
        await checkpoint.save("42:req", valid_status())

@pytest.mark.asyncio
async def test_cleanup_graph_delegates_to_official_saver():
    saver = FakeSaver()
    checkpoint = checkpoint_with(FakePool(), saver)
    checkpoint.available = True

    await checkpoint.cleanup_graph("42:req")

    assert saver.deleted == ["42:req"]

@pytest.mark.asyncio
async def test_close_cancels_cleanup_task_and_closes_pool():
    pool = FakePool()
    checkpoint = checkpoint_with(pool, FakeSaver())
    started = asyncio.Event()

    async def wait_forever():
        started.set()
        await asyncio.Event().wait()

    cleanup_task = asyncio.create_task(wait_forever())
    checkpoint._cleanup_task = cleanup_task
    await started.wait()

    await checkpoint.close()

    assert cleanup_task.cancelled()
    assert pool.closed is True
```

Add a `valid_status()` helper returning all required whitelist fields so save-failure tests do not duplicate payloads.

Assertions must verify stable error text and must not inspect or log the connection URL.

- [ ] **Step 6: Run focused tests**

```powershell
cd ai-service
uv run pytest tests/test_postgres_checkpoint.py tests/test_gateway_and_config.py tests/test_api.py -q
uv run python -m compileall -q src
```

Expected: all selected tests pass and compileall exits 0.

- [ ] **Step 7: Commit Agent A's package**

```powershell
git add ai-service/src/ai_service/infrastructure/postgres_checkpoint.py ai-service/tests/test_postgres_checkpoint.py
git commit -m "fix: 避免 PostgreSQL checkpoint 单连接池清理阻塞"
```

## Task 2: Expand opt-in real PostgreSQL runtime coverage

**Owner:** Agent B

**Files:**
- Create: `ai-service/tests/test_postgres_checkpoint_runtime_integration.py`

- [ ] **Step 1: Create an environment-gated integration module**

Use the same opt-in contract as the existing integration test, but do not import or print the URL:

```python
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from ai_service.infrastructure.postgres_checkpoint import PostgresCheckpoint

pytestmark = pytest.mark.skipif(
    os.getenv("AI_SERVICE_POSTGRES_INTEGRATION") != "true",
    reason="set AI_SERVICE_POSTGRES_INTEGRATION=true to use real PostgreSQL",
)

POSTGRES_URL = os.getenv(
    "AI_SERVICE_CHECKPOINT_POSTGRES_URL",
    "postgresql://postgres:replace-with-password@localhost:5432/yu_ai_checkpoint",
)
```

Every test must generate `thread_id = f"runtime-{uuid.uuid4()}"` and clean only that ID in `finally`.

Define the shared helpers in the same module:

```python
def make_checkpoint(*, auto_setup: bool, max_size: int) -> PostgresCheckpoint:
    return PostgresCheckpoint(
        POSTGRES_URL,
        required=True,
        auto_setup=auto_setup,
        ttl_seconds=3600,
        pool_min_size=1,
        pool_max_size=max_size,
    )


def status(*, request_id: str, node: str, repair_count: int) -> dict[str, object]:
    return {
        "node": node,
        "requestId": request_id,
        "appId": 42,
        "codeGenType": "HTML",
        "qualityPassed": None,
        "repairCount": repair_count,
        "toolCallCount": 0,
    }
```

- [ ] **Step 2: Add setup idempotency and auto-setup-off tests**

Add:

```python
@pytest.mark.asyncio
async def test_setup_is_idempotent_and_auto_setup_false_uses_existing_schema():
    setup_checkpoint = make_checkpoint(auto_setup=True, max_size=2)
    await setup_checkpoint.start()
    await setup_checkpoint.setup_schema()
    await setup_checkpoint.close()

    runtime_checkpoint = make_checkpoint(auto_setup=False, max_size=2)
    await runtime_checkpoint.start()
    try:
        assert runtime_checkpoint.available is True
        assert await runtime_checkpoint.ping() is True
    finally:
        await runtime_checkpoint.close()
```

- [ ] **Step 3: Add the real single-connection expiration test**

Create the wrapper with `pool_min_size=1` and `pool_max_size=1`, write an official checkpoint plus status, force `expires_at` into the past through a separate connection, then run:

```python
await asyncio.wait_for(checkpoint.cleanup_expired(), timeout=5.0)
```

Assert the official saver returns no tuple and the status query returns no row. This test is the real regression for the nested-connection risk.

- [ ] **Step 4: Add upsert and terminal cleanup boundary tests**

Test status twice for the same thread:

```python
await checkpoint.save(
    thread_id,
    status(request_id=thread_id, node="input_guard", repair_count=0),
)
await checkpoint.save(
    thread_id,
    status(request_id=thread_id, node="quality_review", repair_count=1),
)
```

Read through a new `AsyncConnection` and assert the latest node and repair count.

For terminal cleanup, write an official checkpoint and status, call `cleanup_graph(thread_id)`, then assert:

- official graph checkpoint is gone;
- `ai_workflow_status` remains until expiration cleanup;
- after forcing expiration and calling `cleanup_expired()`, the status row is gone.

- [ ] **Step 5: Verify default skip does not connect**

```powershell
cd ai-service
Remove-Item Env:AI_SERVICE_POSTGRES_INTEGRATION -ErrorAction SilentlyContinue
uv run pytest tests/test_postgres_checkpoint_runtime_integration.py -q
```

Expected: the module is skipped and no database connection is attempted.

- [ ] **Step 6: Commit Agent B's package**

```powershell
git add ai-service/tests/test_postgres_checkpoint_runtime_integration.py
git commit -m "test: 扩展 PostgreSQL checkpoint 运行集成覆盖"
```

Do not claim the real tests pass until the user prepares the independent database and the opt-in run exits 0.

## Task 3: Add a secret-safe PostgreSQL runtime verification script

**Owner:** Agent C

**Files:**
- Create: `scripts/verify-postgres-checkpoint-runtime.ps1`
- Create: `scripts/postgres-checkpoint-validation.tests.ps1`

- [ ] **Step 1: Write failing static expectations**

Create `postgres-checkpoint-validation.tests.ps1` with strict mode and assertions that the runtime script contains:

```powershell
foreach ($marker in @(
    '[switch]$Execute',
    'AI_SERVICE_CHECKPOINT_POSTGRES_URL',
    'AI_SERVICE_POSTGRES_INTEGRATION',
    'ai_service.infrastructure.checkpoint_setup',
    'test_postgres_checkpoint_integration.py',
    'test_postgres_checkpoint_runtime_integration.py',
    'target/ai-validation/postgres-checkpoint-runtime.json',
    'manualValidationRequired'
)) {
    if ($source -notmatch [regex]::Escape($marker)) {
        throw "PostgreSQL runtime verification misses $marker"
    }
}
```

Reject credential output patterns:

```powershell
foreach ($forbidden in @(
    'Write-Host.*CHECKPOINT_POSTGRES_URL',
    'Write-Output.*CHECKPOINT_POSTGRES_URL',
    'ConvertTo-Json.*CHECKPOINT_POSTGRES_URL',
    'password',
    'conninfo'
)) {
    if ($source -match $forbidden) {
        throw "PostgreSQL runtime verification may expose credentials: $forbidden"
    }
}
```

Run the missing-script check and confirm it fails.

- [ ] **Step 2: Implement dry-run and execution preconditions**

`verify-postgres-checkpoint-runtime.ps1` starts with:

```powershell
param(
    [switch]$Execute,
    [string]$OutputPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $Execute) {
    Write-Host 'Dry run only. No PostgreSQL connection or report file will be created.'
    Write-Host 'Set AI_SERVICE_CHECKPOINT_POSTGRES_URL in the current process, then rerun with -Execute.'
    return
}

if ([string]::IsNullOrWhiteSpace($env:AI_SERVICE_CHECKPOINT_POSTGRES_URL)) {
    throw 'AI_SERVICE_CHECKPOINT_POSTGRES_URL must be set in the current process.'
}
```

Never accept a URL parameter and never echo the environment value.

- [ ] **Step 3: Implement bounded validation steps**

Reuse a local `Invoke-ValidationStep` helper that records only command labels and exit codes. Execute from `ai-service`:

```text
uv run python -m ai_service.infrastructure.checkpoint_setup
uv run pytest tests/test_postgres_checkpoint_integration.py tests/test_postgres_checkpoint_runtime_integration.py
uv run pytest tests/test_postgres_checkpoint.py tests/test_gateway_and_config.py tests/test_api.py
uv run python -m compileall -q src
uv lock --check
```

Set `AI_SERVICE_POSTGRES_INTEGRATION=true` only around the real integration step and restore its previous process value in `finally`.

Write a report shaped as:

```powershell
$report = [ordered]@{
    generatedAtUtc = [DateTime]::UtcNow.ToString('o')
    commit = (git -C $repositoryRoot rev-parse HEAD).Trim()
    overallStatus = if ($failed.Count -eq 0) { 'passed' } else { 'failed' }
    steps = @($steps)
    manualValidationRequired = @(
        'required mode while PostgreSQL is unavailable',
        'optional mode while PostgreSQL is unavailable',
        'production AUTO_SETUP=false and restricted runtime role'
    )
}
```

Do not add environment variables, raw output, DSN or exception bodies to the JSON.

- [ ] **Step 4: Add behavior tests that do not connect to PostgreSQL**

The validation test must:

- run dry-run and assert exit 0, `Dry run only`, and no report file;
- run `-Execute` with the URL variable absent and assert nonzero plus the stable missing-variable message;
- confirm the script never creates the default report in either case;
- run on Windows PowerShell 5.1 and PowerShell 7 when both are installed.

- [ ] **Step 5: Run script tests**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/postgres-checkpoint-validation.tests.ps1
pwsh -NoProfile -File scripts/postgres-checkpoint-validation.tests.ps1
```

Expected: both exit 0 without connecting to PostgreSQL.

- [ ] **Step 6: Commit Agent C's package**

```powershell
git add scripts/verify-postgres-checkpoint-runtime.ps1 scripts/postgres-checkpoint-validation.tests.ps1
git commit -m "test: 增加 PostgreSQL checkpoint 专项验收门"
```

## Task 4: Integrate the dedicated gate into central verification

**Owner:** Integration Agent, sequential after Tasks 1-3

**Files:**
- Modify: `scripts/verify-langgraph-real-gate.ps1`
- Modify: `scripts/ai-validation-scripts.tests.ps1`

- [ ] **Step 1: Update static expectations first**

Require both new scripts in `$expectedScripts` and update markers so the central verifier must reference:

```text
verify-postgres-checkpoint-runtime.ps1
postgres-checkpoint-validation.tests.ps1
-IncludePostgres
```

The static test must still reject credential-related output fields.

- [ ] **Step 2: Delegate `-IncludePostgres` to the dedicated gate**

Replace the direct pytest step with:

```powershell
if ($IncludePostgres) {
    $steps.Add((Invoke-ValidationStep `
        -Name 'postgres-checkpoint-runtime' `
        -CommandLabel 'scripts/verify-postgres-checkpoint-runtime.ps1 -Execute' `
        -FilePath $powerShell `
        -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            (Join-Path $PSScriptRoot 'verify-postgres-checkpoint-runtime.ps1'),
            '-Execute'
        )))
}
```

Do not pass the URL on the command line; the child process inherits the current process environment.

- [ ] **Step 3: Add the dedicated static test to the default script checks**

Execute `postgres-checkpoint-validation.tests.ps1` from `ai-validation-scripts.tests.ps1` and assert its exit code is 0.

- [ ] **Step 4: Run central script checks**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ai-validation-scripts.tests.ps1
pwsh -NoProfile -File scripts/ai-validation-scripts.tests.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1
```

Expected: both static checks pass; dry-run describes `-IncludePostgres`; no report is written.

- [ ] **Step 5: Commit central integration**

```powershell
git add scripts/verify-langgraph-real-gate.ps1 scripts/ai-validation-scripts.tests.ps1
git commit -m "test: 集成 PostgreSQL checkpoint 专项门禁"
```

## Task 5: Update runtime documentation and handoff

**Owner:** Integration Agent, sequential

**Files:**
- Modify: `AGENTS.md`
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-startup.md`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: Document the fixed one-connection lifecycle**

State that expiration cleanup reads IDs and releases the connection before official graph deletion. Record that successful graph deletion precedes status deletion and failed graph deletion retains the status row.

- [ ] **Step 2: Separate storage support from workflow resume support**

Use this explicit boundary:

```text
PostgreSQL provides LangGraph checkpoint persistence and cleanup.
The service does not yet expose a resume/recover API or promise replay-free event continuation after a process crash.
```

Do not describe checkpoint persistence as an implemented user-visible resume feature.

- [ ] **Step 3: Document the dedicated verification command**

Add:

```powershell
# Dry-run: no database connection
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-postgres-checkpoint-runtime.ps1

# Real execution: URL is injected only into the current process
$env:AI_SERVICE_CHECKPOINT_POSTGRES_URL = '<set-locally>'
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-postgres-checkpoint-runtime.ps1 -Execute
```

Do not include a real URL, password, container environment dump or volume path.

- [ ] **Step 4: Fix the P0 status wording**

Distinguish the previously user-confirmed generation/rollback P0 from the still-pending PostgreSQL runtime P0. Until Task 7 succeeds, write `implemented, real runtime gate not yet executed`.

- [ ] **Step 5: Preserve the long-term-memory decision**

State that `PostgresStore` remains disabled and is not part of this runtime-gate plan.

- [ ] **Step 6: Commit documentation**

```powershell
git add AGENTS.md ai-service/README.md doc/ai-service-startup.md doc/ai-service-phase-one-handoff.md
git commit -m "docs: 更新 PostgreSQL checkpoint 运行验收说明"
```

## Task 6: Run complete offline verification

**Owner:** Root Agent

**Files:**
- Verify only; do not modify source unless a failure has a proven root cause.

- [ ] **Step 1: Verify default PostgreSQL integration tests skip without connecting**

```powershell
cd ai-service
Remove-Item Env:AI_SERVICE_POSTGRES_INTEGRATION -ErrorAction SilentlyContinue
uv run pytest tests/test_postgres_checkpoint_integration.py tests/test_postgres_checkpoint_runtime_integration.py -q
```

Expected: both opt-in modules skip.

- [ ] **Step 2: Run Python full verification**

```powershell
cd ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Expected: all offline tests pass; only opt-in real PostgreSQL tests skip.

- [ ] **Step 3: Run both PowerShell script suites**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/postgres-checkpoint-validation.tests.ps1
pwsh -NoProfile -File scripts/postgres-checkpoint-validation.tests.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ai-validation-scripts.tests.ps1
pwsh -NoProfile -File scripts/ai-validation-scripts.tests.ps1
```

- [ ] **Step 4: Run the default unified gate**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1 -Execute
```

Expected: all default steps are `passed`; PostgreSQL real runtime remains explicitly not selected.

- [ ] **Step 5: Run source and secret scans**

```powershell
rg -n "RedisCheckpoint|RedisGraphSaver|redis_enabled|redis_required|redis_url|redis\.asyncio" ai-service/src ai-service/tests ai-service/pyproject.toml
rg -n "PostgresStore|AsyncPostgresStore|BaseStore" ai-service/src ai-service/tests
rg -n "postgresql://" AGENTS.md ai-service doc docs scripts
git diff --check
git status --short --branch
```

Expected: no runtime Redis checkpoint, no long-term store, and no whitespace errors. Manually inspect every PostgreSQL URL match: only documented placeholders or non-secret defaults are allowed; no real password, personal host or production address may appear. Only task files may be changed.

## Task 7: Execute the user-assisted real PostgreSQL gate

**Owner:** Root Agent plus user, only after explicit environment confirmation

**Files:**
- Runtime validation only; generated reports remain under ignored `target/ai-validation/`.

- [ ] **Step 1: Ask the user to prepare the independent database**

The user or database administrator runs equivalent SQL inside the selected PostgreSQL instance:

```sql
CREATE ROLE yu_ai_checkpoint LOGIN PASSWORD '<set-outside-git>';
CREATE DATABASE yu_ai_checkpoint OWNER yu_ai_checkpoint;
```

If the role or database already exists, inspect and reuse it rather than running destructive replacement SQL. The agent must not request the real password in chat.

- [ ] **Step 2: Ask the user to inject the URL locally**

The user sets the URL in the terminal that will run validation:

```powershell
$env:AI_SERVICE_CHECKPOINT_POSTGRES_URL = 'postgresql://<local-user>:<local-password>@localhost:5432/yu_ai_checkpoint'
```

Do not print the value. Confirm only whether the variable is set:

```powershell
if ([string]::IsNullOrWhiteSpace($env:AI_SERVICE_CHECKPOINT_POSTGRES_URL)) {
    throw 'PostgreSQL checkpoint URL is not set.'
}
```

- [ ] **Step 3: Run the dedicated real gate**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-postgres-checkpoint-runtime.ps1 -Execute
```

Expected: setup, both real integration modules, focused offline tests, compileall, and lock check pass; the report contains no secret fields.

- [ ] **Step 4: Run the central gate with PostgreSQL enabled**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1 -Execute -IncludePostgres
```

Expected: the default six steps and the delegated PostgreSQL runtime step pass.

- [ ] **Step 5: Perform optional/required down checks only with approval**

Do not stop or restart the user's Docker container automatically. If the user approves a disposable outage window, validate:

```text
disabled       -> service starts, ready 200, no PostgreSQL connection
optional down  -> service starts, ready 503, no graph saver
required down  -> service startup fails with stable sanitized error
```

Record only mode, status, stable error type and duration.

- [ ] **Step 6: Update final evidence and commit**

After the real run, update the handoff with the exact date, commands, pass/fail counts and any blocked manual modes. Never copy the DSN or raw database errors.

```powershell
git add doc/ai-service-phase-one-handoff.md
git commit -m "docs: 记录 PostgreSQL checkpoint 真实验收"
```

If the user cannot prepare the database, mark Task 7 `blocked` with the direct reason. Do not mark the runtime gate passed and do not block Tasks 1-6 from being merged.
