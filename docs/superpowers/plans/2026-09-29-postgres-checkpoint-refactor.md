# PostgreSQL Checkpoint Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Python AI service's custom Redis checkpoint implementation with the official PostgreSQL checkpointer while retaining short-lived sanitized workflow status, terminal cleanup, optional/required degradation, and no long-term memory.

**Architecture:** `checkpoint.py` keeps the workflow-facing protocol and disabled implementation. A new `postgres_checkpoint.py` owns one Psycopg async pool, `AsyncPostgresSaver`, the sanitized `ai_workflow_status` table, readiness, and expiration cleanup. A separate `checkpoint_setup.py` initializes tables without starting FastAPI or model clients; the workflow continues to depend only on `CheckpointStore`.

**Tech Stack:** Python 3.12, FastAPI, LangGraph 0.6.x, `langgraph-checkpoint-postgres` 3.0.x, Psycopg 3 async pool, PostgreSQL in the user's existing local Docker container, pytest, uv.

---

## File map

- Modify `ai-service/pyproject.toml`: replace Redis checkpoint dependency with PostgreSQL checkpoint and Psycopg dependencies.
- Modify `ai-service/uv.lock`: lock compatible PostgreSQL packages.
- Modify `ai-service/src/ai_service/config.py`: replace Redis checkpoint settings and validate pool bounds.
- Modify `ai-service/src/ai_service/infrastructure/checkpoint.py`: retain only protocol and disabled implementation.
- Create `ai-service/src/ai_service/infrastructure/postgres_checkpoint.py`: pool, official saver, status upsert, expiration and readiness.
- Create `ai-service/src/ai_service/infrastructure/checkpoint_setup.py`: standalone idempotent database setup command.
- Modify `ai-service/src/ai_service/app.py`: construct PostgreSQL or disabled checkpoint implementation.
- Modify `ai-service/tests/conftest.py`: use new checkpoint settings in fixtures.
- Modify `ai-service/tests/test_gateway_and_config.py`: remove Redis implementation tests and add configuration coverage.
- Create `ai-service/tests/test_postgres_checkpoint.py`: isolated lifecycle, status and cleanup tests using fakes.
- Create `ai-service/tests/test_postgres_checkpoint_integration.py`: opt-in real PostgreSQL tests.
- Modify `ai-service/tests/test_package_structure.py`: assert the new public module and class.
- Modify `scripts/verify-langgraph-real-gate.ps1`: include Python compile, pytest and lock checks.
- Modify configuration and handoff documentation listed in Task 6.

## Task 1: Add compatible dependencies and checkpoint configuration

**Files:**
- Modify: `ai-service/pyproject.toml`
- Modify: `ai-service/uv.lock`
- Modify: `ai-service/src/ai_service/config.py`
- Modify: `ai-service/tests/conftest.py`
- Modify: `ai-service/tests/test_gateway_and_config.py`

- [ ] **Step 1: Replace fixture settings and write failing configuration tests**

Update the shared fixture to stop passing Redis settings:

```python
return Settings(
    internal_bearer_token="test-secret",
    spring_gateway_base_url="http://spring.test/api/internal/ai-tools",
    spring_gateway_bearer_token="spring-secret",
    checkpoint_enabled=False,
    checkpoint_required=False,
)
```

Add tests:

```python
def test_checkpoint_postgres_defaults():
    settings = Settings(
        internal_bearer_token="test-secret",
        spring_gateway_base_url="http://spring.test/api/internal/ai-tools",
        spring_gateway_bearer_token="spring-secret",
    )
    assert settings.checkpoint_enabled is True
    assert settings.checkpoint_required is False
    assert str(settings.checkpoint_postgres_url).startswith("postgresql://")
    assert settings.checkpoint_auto_setup is True
    assert settings.checkpoint_pool_min_size == 1
    assert settings.checkpoint_pool_max_size == 5


def test_checkpoint_pool_max_must_not_be_smaller_than_min():
    with pytest.raises(ValueError, match="checkpoint_pool_max_size"):
        Settings(
            internal_bearer_token="test-secret",
            spring_gateway_base_url="http://spring.test/api/internal/ai-tools",
            spring_gateway_bearer_token="spring-secret",
            checkpoint_pool_min_size=4,
            checkpoint_pool_max_size=2,
        )
```

- [ ] **Step 2: Run the tests and confirm the old settings fail**

Run:

```powershell
cd ai-service
uv run pytest tests/test_gateway_and_config.py -q
```

Expected: failures because `Settings` does not yet define PostgreSQL checkpoint fields and fixtures still reference Redis fields.

- [ ] **Step 3: Implement new settings and remove old Redis fields**

Use a model validator for pool bounds:

```python
from pydantic import Field, HttpUrl, model_validator

checkpoint_enabled: bool = True
checkpoint_required: bool = False
checkpoint_postgres_url: str = "postgresql://postgres:postgres@localhost:5432/yu_ai_checkpoint"
checkpoint_auto_setup: bool = True
checkpoint_ttl_seconds: int = Field(default=86400, ge=60)
checkpoint_pool_min_size: int = Field(default=1, ge=1, le=20)
checkpoint_pool_max_size: int = Field(default=5, ge=1, le=50)

@model_validator(mode="after")
def validate_checkpoint_pool(self) -> "Settings":
    if self.checkpoint_pool_max_size < self.checkpoint_pool_min_size:
        raise ValueError("checkpoint_pool_max_size must be greater than or equal to checkpoint_pool_min_size")
    return self
```

Delete `redis_enabled`, `redis_required`, and `redis_url` from `Settings`.

- [ ] **Step 4: Replace dependencies and refresh the lock**

In `pyproject.toml`, remove `redis>=5.2,<7` and add:

```toml
"langgraph-checkpoint-postgres>=3.0.1,<3.1",
"psycopg[binary]>=3.2,<4",
"psycopg-pool>=3.2,<4",
```

Run:

```powershell
cd ai-service
uv lock
uv sync --frozen --python 3.12
uv lock --check
```

Expected: lock resolves `langgraph-checkpoint-postgres` on the 3.0.x line and retains a compatible `langgraph-checkpoint` 3.x package.

- [ ] **Step 5: Run focused tests and commit**

```powershell
cd ai-service
uv run pytest tests/test_gateway_and_config.py -q
uv run python -m compileall -q src
git add ai-service/pyproject.toml ai-service/uv.lock ai-service/src/ai_service/config.py ai-service/tests/conftest.py ai-service/tests/test_gateway_and_config.py
git commit -m "refactor: 配置 PostgreSQL checkpoint"
```

Expected: focused tests and compileall pass.

## Task 2: Introduce the PostgreSQL checkpoint lifecycle

**Files:**
- Modify: `ai-service/src/ai_service/infrastructure/checkpoint.py`
- Create: `ai-service/src/ai_service/infrastructure/postgres_checkpoint.py`
- Create: `ai-service/tests/test_postgres_checkpoint.py`
- Modify: `ai-service/tests/test_package_structure.py`

- [ ] **Step 1: Write failing lifecycle tests with fake pool and saver**

Create fakes that expose only the methods used by production code:

```python
class FakePool:
    def __init__(self):
        self.opened = False
        self.closed = False

    async def open(self, *, wait: bool, timeout: float) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True


class FakeSaver:
    def __init__(self):
        self.setup_calls = 0
        self.deleted: list[str] = []

    async def setup(self) -> None:
        self.setup_calls += 1

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)
```

Test that `start()` opens the pool, runs setup when enabled, sets `available=True`, exposes the saver, and `close()` closes the pool. Also test that `auto_setup=False` skips setup but still verifies schema.

- [ ] **Step 2: Run tests and confirm imports fail**

```powershell
cd ai-service
uv run pytest tests/test_postgres_checkpoint.py tests/test_package_structure.py -q
```

Expected: failure because `postgres_checkpoint.py` and `PostgresCheckpoint` do not exist.

- [ ] **Step 3: Reduce `checkpoint.py` to the stable protocol**

Keep `CheckpointStore` and `DisabledCheckpoint`. Delete `_component`, `RedisGraphSaver`, Redis imports and `RedisCheckpoint`. The protocol remains:

```python
class CheckpointStore(Protocol):
    available: bool
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def save(self, thread_id: str, state: dict[str, Any]) -> None: ...
    async def cleanup_graph(self, thread_id: str) -> None: ...
    async def ping(self) -> bool: ...
    def get_graph_saver(self) -> BaseCheckpointSaver | None: ...
```

- [ ] **Step 4: Implement connection lifecycle and official saver**

Create `PostgresCheckpoint` with injectable factories:

```python
class PostgresCheckpoint:
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
        saver_factory: Callable[[AsyncConnectionPool], AsyncPostgresSaver] = AsyncPostgresSaver,
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
        self._graph_saver = saver_factory(self._pool)
        self._required = required
        self._auto_setup = auto_setup
        self._ttl_seconds = ttl_seconds
        self.available = False
```

`start()` must open the pool, optionally call `setup()`, verify required tables, run one expiration cleanup, then set available. On failure, close the pool; required mode raises `RuntimeError("PostgreSQL checkpoint is required but unavailable")`, optional mode logs a sanitized warning.

- [ ] **Step 5: Add `ping`, `cleanup_graph` and `close` behavior**

`ping()` executes `SELECT 1`. `cleanup_graph()` calls `adelete_thread()` only when available; failure marks unavailable and logs without raising because terminal cleanup must not reverse the business result. `close()` cancels the cleanup task before closing the pool.

- [ ] **Step 6: Run lifecycle tests and commit**

```powershell
cd ai-service
uv run pytest tests/test_postgres_checkpoint.py tests/test_package_structure.py -q
uv run python -m compileall -q src
git add ai-service/src/ai_service/infrastructure/checkpoint.py ai-service/src/ai_service/infrastructure/postgres_checkpoint.py ai-service/tests/test_postgres_checkpoint.py ai-service/tests/test_package_structure.py
git commit -m "refactor: 接入官方 PostgreSQL checkpointer"
```

## Task 3: Preserve sanitized status and TTL cleanup

**Files:**
- Modify: `ai-service/src/ai_service/infrastructure/postgres_checkpoint.py`
- Modify: `ai-service/tests/test_postgres_checkpoint.py`

- [ ] **Step 1: Write failing status whitelist and upsert tests**

Use a fake async connection/cursor to capture SQL and parameters. Assert that:

```python
state = {
    "node": "quality_review",
    "requestId": "req-1",
    "appId": 42,
    "codeGenType": "HTML",
    "qualityPassed": True,
    "repairCount": 1,
    "toolCallCount": 0,
}
await checkpoint.save("42:req-1", state)
```

performs one `INSERT ... ON CONFLICT (thread_id) DO UPDATE`, uses `Jsonb(state)`, and sets `expires_at` from `checkpoint_ttl_seconds`. Add a failure test proving an extra `artifact` or `source` key raises `ValueError` before SQL execution.

- [ ] **Step 2: Write failing expiration cleanup tests**

Make the fake `DELETE FROM ai_workflow_status ... RETURNING thread_id` return two expired thread IDs. Assert the saver receives both IDs, one failure does not skip the other, and successful status rows are removed independently of graph deletion retries.

- [ ] **Step 3: Run the new tests and verify failure**

```powershell
cd ai-service
uv run pytest tests/test_postgres_checkpoint.py -q
```

Expected: failing tests because status schema, upsert and expiration cleanup are not implemented.

- [ ] **Step 4: Add schema and strict status normalization**

Define constants:

```python
STATUS_FIELDS = frozenset({
    "node", "requestId", "appId", "codeGenType",
    "qualityPassed", "repairCount", "toolCallCount",
})
```

Reject missing required identifiers and unknown keys. Execute idempotent DDL for `ai_workflow_status` and `idx_ai_workflow_status_expires_at` only from `setup_schema()`.

- [ ] **Step 5: Implement upsert and bounded cleanup loop**

Use `INSERT ... ON CONFLICT` and `Jsonb`. `cleanup_expired()` deletes expired status rows with `RETURNING thread_id`, then calls official `adelete_thread()` for each. Start a background loop after successful startup:

```python
interval = max(60, min(900, self._ttl_seconds // 4))
```

The loop sleeps first, catches cancellation, and never logs URLs, payloads or checkpoint content.

- [ ] **Step 6: Run focused tests and commit**

```powershell
cd ai-service
uv run pytest tests/test_postgres_checkpoint.py -q
git add ai-service/src/ai_service/infrastructure/postgres_checkpoint.py ai-service/tests/test_postgres_checkpoint.py
git commit -m "feat: 保留 PostgreSQL checkpoint 状态摘要"
```

## Task 4: Wire the application and standalone setup command

**Files:**
- Modify: `ai-service/src/ai_service/app.py`
- Create: `ai-service/src/ai_service/infrastructure/checkpoint_setup.py`
- Modify: `ai-service/tests/test_gateway_and_config.py`
- Modify: `ai-service/tests/test_api.py`
- Modify: `ai-service/tests/test_package_structure.py`

- [ ] **Step 1: Write failing application factory tests**

Monkeypatch `PostgresCheckpoint` and assert enabled settings pass the URL, required, auto-setup, TTL and pool sizes. Assert disabled settings construct `DisabledCheckpoint`. Existing injected `checkpoint=` tests must remain unchanged.

- [ ] **Step 2: Write a failing setup-command test**

Patch `get_settings` and `PostgresCheckpoint`, call `checkpoint_setup.main_async()`, and assert the instance is created with `required=True`, `auto_setup=True`, then started and closed without creating a model or Spring gateway.

- [ ] **Step 3: Implement application wiring**

Replace the Redis branch in `create_app()`:

```python
checkpoint_store = checkpoint or (
    PostgresCheckpoint(
        config.checkpoint_postgres_url,
        required=config.checkpoint_required,
        auto_setup=config.checkpoint_auto_setup,
        ttl_seconds=config.checkpoint_ttl_seconds,
        pool_min_size=config.checkpoint_pool_min_size,
        pool_max_size=config.checkpoint_pool_max_size,
    )
    if config.checkpoint_enabled
    else DisabledCheckpoint()
)
```

- [ ] **Step 4: Implement the standalone setup module**

Provide:

```python
async def main_async() -> None:
    settings = get_settings()
    checkpoint = PostgresCheckpoint(
        settings.checkpoint_postgres_url,
        required=True,
        auto_setup=True,
        ttl_seconds=settings.checkpoint_ttl_seconds,
        pool_min_size=settings.checkpoint_pool_min_size,
        pool_max_size=settings.checkpoint_pool_max_size,
    )
    await checkpoint.start()
    await checkpoint.close()


def main() -> None:
    asyncio.run(main_async())
```

Add the `if __name__ == "__main__": main()` entrypoint.

- [ ] **Step 5: Verify ready and terminal behavior**

Run existing API tests, especially checkpoint cleanup, cancellation, ready=503, and “published artifact remains completed when graph checkpoint fails”. Do not weaken those assertions.

```powershell
cd ai-service
uv run pytest tests/test_api.py tests/test_gateway_and_config.py tests/test_package_structure.py -q
```

- [ ] **Step 6: Commit application wiring**

```powershell
git add ai-service/src/ai_service/app.py ai-service/src/ai_service/infrastructure/checkpoint_setup.py ai-service/tests/test_api.py ai-service/tests/test_gateway_and_config.py ai-service/tests/test_package_structure.py
git commit -m "feat: 启用 PostgreSQL checkpoint 生命周期"
```

## Task 5: Add opt-in Docker PostgreSQL integration coverage

**Files:**
- Create: `ai-service/tests/test_postgres_checkpoint_integration.py`

- [ ] **Step 1: Add an environment-gated integration fixture**

```python
pytestmark = pytest.mark.skipif(
    os.getenv("AI_SERVICE_POSTGRES_INTEGRATION") != "true",
    reason="set AI_SERVICE_POSTGRES_INTEGRATION=true to use real PostgreSQL",
)

POSTGRES_URL = os.getenv(
    "AI_SERVICE_CHECKPOINT_POSTGRES_URL",
    "postgresql://postgres:postgres@localhost:5432/yu_ai_checkpoint",
)
```

Use a random thread ID per test and delete only that thread/status row during teardown.

- [ ] **Step 2: Cover real official checkpoint operations**

Create an `AsyncPostgresSaver`, call setup, write a minimal checkpoint, read it with `aget_tuple`, list it with `alist`, write pending channel data, and delete with `adelete_thread`. Assert repeated setup is idempotent.

- [ ] **Step 3: Cover the wrapper and expiration cleanup**

Start `PostgresCheckpoint`, save a sanitized status, query it through a separate Psycopg connection, force `expires_at` into the past, call `cleanup_expired()`, and assert both status and graph rows for the random thread are gone.

- [ ] **Step 4: Verify default skip and optional real execution**

```powershell
cd ai-service
Remove-Item Env:AI_SERVICE_POSTGRES_INTEGRATION -ErrorAction SilentlyContinue
uv run pytest tests/test_postgres_checkpoint_integration.py -q
```

Expected: all integration tests skipped and no database connection attempted.

When the user's Docker PostgreSQL and disposable `yu_ai_checkpoint` database are ready:

```powershell
$env:AI_SERVICE_POSTGRES_INTEGRATION = "true"
$env:AI_SERVICE_CHECKPOINT_POSTGRES_URL = "postgresql://<user>:<password>@localhost:5432/yu_ai_checkpoint"
uv run pytest tests/test_postgres_checkpoint_integration.py -q
```

Expected: all tests pass. Never echo or persist the URL.

- [ ] **Step 5: Commit integration coverage**

```powershell
git add ai-service/tests/test_postgres_checkpoint_integration.py
git commit -m "test: 覆盖 PostgreSQL checkpoint 集成"
```

## Task 6: Update configuration, runbooks and handoff

**Files:**
- Modify: `ai-service/.env.example`
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-startup.md`
- Modify: `doc/ai-service-langchain-langgraph-refactor-design.md`
- Modify: `doc/ai-service-phase-one-handoff.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Replace Redis checkpoint documentation with current PostgreSQL facts**

Document:

- `yu_ai_checkpoint` is an independent PostgreSQL database owned by Python AI service.
- Spring business MySQL and Redis database 1 remain unchanged.
- The repository does not manage the user's existing Docker container.
- `AI_SERVICE_CHECKPOINT_POSTGRES_URL` contains a placeholder only.
- `uv run python -m ai_service.infrastructure.checkpoint_setup` initializes tables.
- The locked serializer has no `LANGGRAPH_STRICT_MSGPACK` switch. Document the explicit
  `JsonPlusSerializer(allowed_json_modules=())` configuration, disabled pickle fallback,
  and the requirement that only the AI service may write the checkpoint database.
- `AUTO_SETUP=true` is for local use; production should initialize separately and run without DDL permission.

- [ ] **Step 2: Add local database bootstrap examples without secrets**

Use placeholders:

```sql
CREATE ROLE yu_ai_checkpoint LOGIN PASSWORD '<set-outside-git>';
CREATE DATABASE yu_ai_checkpoint OWNER yu_ai_checkpoint;
```

Do not add a Docker Compose file, container name, real password or volume path.

- [ ] **Step 3: Record the long-term memory decision**

State in README and handoff:

- `PostgresStore` is not enabled.
- Spring chat history remains the only cross-request conversational context.
- Re-evaluate only for explicit cross-application preferences or structured application decisions with view/edit/delete/expiry product semantics.

- [ ] **Step 4: Update the handoff from planned to implemented only after tests pass**

Replace “design approved, not implemented” with exact implementation and verification facts. Do not claim the opt-in PostgreSQL integration passed unless Task 5 was actually executed against Docker.

- [ ] **Step 5: Verify documentation consistency and commit**

```powershell
rg -n "Python checkpoint Redis|AI_SERVICE_REDIS_ENABLED|AI_SERVICE_REDIS_REQUIRED|AI_SERVICE_REDIS_URL|Redis checkpoint" AGENTS.md ai-service/README.md ai-service/.env.example doc
git diff --check
git add AGENTS.md ai-service/.env.example ai-service/README.md doc/ai-service-startup.md doc/ai-service-langchain-langgraph-refactor-design.md doc/ai-service-phase-one-handoff.md
git commit -m "docs: 迁移 AI checkpoint 到 PostgreSQL"
```

Expected: remaining Redis references describe Spring tool idempotency or historical migration only.

## Task 7: Extend the unified gate and run final verification

**Files:**
- Modify: `scripts/verify-langgraph-real-gate.ps1`
- Modify: `scripts/ai-validation-scripts.tests.ps1`
- Modify: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: Write failing static script assertions**

Require the unified gate to contain Python steps for:

```text
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Add an optional `-IncludePostgres` switch that runs only `test_postgres_checkpoint_integration.py` with `AI_SERVICE_POSTGRES_INTEGRATION=true`. It must never print the database URL.

- [ ] **Step 2: Implement the Python gate steps**

Resolve `uv` from PATH, run commands with working directory `ai-service`, restore environment variables after optional integration execution, and write only status, duration and exit code to the JSON summary.

- [ ] **Step 3: Run PowerShell compatibility tests**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/ai-validation-scripts.tests.ps1
pwsh -NoProfile -File scripts/ai-validation-scripts.tests.ps1
```

Expected: both pass.

- [ ] **Step 4: Run the complete non-PostgreSQL gate**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1 -Execute
```

Expected:

- Java clean compile passes for the current production source count.
- 35 Java focused tests pass.
- PowerShell static checks pass.
- Python compileall passes.
- Full Python pytest passes, with the PostgreSQL integration class skipped unless explicitly enabled.
- `uv lock --check` passes.

- [ ] **Step 5: Run focused direct verification**

```powershell
cd ai-service
uv run pytest tests/test_postgres_checkpoint.py tests/test_gateway_and_config.py tests/test_api.py -q
uv run python -m compileall -q src
uv lock --check
cd ..
git diff --check
git status --short
```

- [ ] **Step 6: Update handoff evidence and commit**

Record exact fresh counts and whether real PostgreSQL integration was run or skipped.

```powershell
git add scripts/verify-langgraph-real-gate.ps1 scripts/ai-validation-scripts.tests.ps1 doc/ai-service-phase-one-handoff.md
git commit -m "test: 纳入 PostgreSQL checkpoint 验收门"
```

## Task 8: Final migration audit

**Files:**
- Review all task files; modify only if the audit finds a defect.

- [ ] **Step 1: Confirm removed runtime dependencies and names**

```powershell
rg -n "RedisCheckpoint|RedisGraphSaver|redis_enabled|redis_required|redis_url|redis\.asyncio" ai-service/src ai-service/tests ai-service/pyproject.toml
```

Expected: no runtime Redis checkpoint implementation remains. Historical text in plans/specs is allowed.

- [ ] **Step 2: Confirm no Store or memory implementation was added**

```powershell
rg -n "PostgresStore|AsyncPostgresStore|BaseStore|memory namespace|vector index" ai-service/src ai-service/tests
```

Expected: no long-term memory runtime code.

- [ ] **Step 3: Confirm secrets and generated data are absent**

```powershell
git status --short
git diff --check
git grep -n "postgresql://[^<]*:[^<]*@" -- ':!docs/superpowers/plans/*' ':!docs/superpowers/specs/*'
```

Expected: only placeholder URLs or test defaults; no local `.env`, database dumps, logs or `target/` artifacts are tracked.

- [ ] **Step 4: Record final branch state**

```powershell
git log --oneline -12
git status --short --branch
```

Expected: clean branch with separate dependency/config, implementation, integration, documentation and gate commits.
