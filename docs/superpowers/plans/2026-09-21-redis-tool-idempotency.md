# Redis Tool Idempotency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the process-local internal AI tool result cache with Redis-backed, scoped idempotency that detects request conflicts and prevents unsafe replay after an indeterminate execution.

**Architecture:** Python sends `appId`, `requestId`, and `toolCallId` as trusted request-level fields. Spring normalizes the tool name, computes a deterministic request fingerprint, and executes through a dedicated Redisson-backed service using a scoped distributed lock plus `RUNNING`/`SUCCEEDED` records with TTL. Redis failures fail closed; artifact publication keeps its independent version-store idempotency.

**Tech Stack:** Java 21, Spring Boot 3.5, Redisson, Jackson, JUnit 5, Mockito, Python 3.12, httpx, pytest

---

### Task 1: Extend The Python-to-Spring Tool Request Contract

**Files:**
- Modify: `ai-service/src/ai_service/infrastructure/spring_tools.py`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/conftest.py`
- Modify: `ai-service/tests/test_gateway_and_config.py`
- Modify: `ai-service/tests/test_api.py`

- [x] **Step 1: Write failing gateway request tests**

Change the gateway test to require request-level scope fields and assert that controlled scope is not duplicated inside `arguments`:

```python
result = await gateway.invoke(
    "project_build",
    {"codeGenType": "VUE_PROJECT"},
    app_id="42",
    request_id="req-1",
    tool_call_id="req-1:build:1",
)

body = json.loads(captured["body"])
assert body == {
    "appId": "42",
    "requestId": "req-1",
    "toolCallId": "req-1:build:1",
    "toolName": "project_build",
    "arguments": {"codeGenType": "VUE_PROJECT"},
}
```

Update `FakeToolGateway.invoke` to record `appId` and `requestId`, then assert every workflow tool call carries `42` and `req-1` at request scope.

- [x] **Step 2: Run focused Python tests and verify they fail**

Run:

```powershell
cd ai-service
uv run pytest tests/test_gateway_and_config.py tests/test_api.py -q
```

Expected: FAIL because `SpringToolGateway.invoke` does not accept `app_id` or `request_id` and the workflow still places `appId` in tool arguments.

- [x] **Step 3: Implement the scoped gateway contract**

Use this signature and body shape in `spring_tools.py`:

```python
async def invoke(
    self,
    name: str,
    arguments: dict[str, Any],
    *,
    app_id: str,
    request_id: str,
    tool_call_id: str,
) -> dict[str, Any]:
    request_body = {
        "appId": app_id,
        "requestId": request_id,
        "toolCallId": tool_call_id,
        "toolName": name,
        "arguments": arguments,
    }
```

Change `GenerationWorkflow` so every Vue, validation, build, and publication invocation passes `state["app_id"]` and `state["request_id"]`. Remove `appId` from tool argument maps. Remove `requestId` from `artifact_publish` arguments because Spring receives it at request scope.

- [x] **Step 4: Run focused and full Python tests**

Run:

```powershell
uv run pytest tests/test_gateway_and_config.py tests/test_api.py -q
uv run pytest
```

Expected: all tests PASS.

- [x] **Step 5: Commit the Python contract change**

```powershell
git add ai-service/src/ai_service/infrastructure/spring_tools.py `
  ai-service/src/ai_service/orchestration/workflow.py `
  ai-service/tests/conftest.py `
  ai-service/tests/test_gateway_and_config.py `
  ai-service/tests/test_api.py
git commit -m "feat: 扩展内部工具请求作用域"
```

### Task 2: Implement The Redis Idempotency Service

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/config/AiEngineProperties.java`
- Create: `src/main/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyService.java`
- Create: `src/test/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyServiceTest.java`

- [x] **Step 1: Write failing service tests**

Cover these behaviors with mocked `RedissonClient`, `RLock`, and `RBucket<String>`:

```java
@Test
void replaysMatchingSuccessfulResultWithoutExecutingAgain() {
    AtomicInteger executions = new AtomicInteger();
    Map<String, Object> first = service.execute(
            42L, "req-1", "call-1", InternalAiTool.FILE_READ,
            Map.of("relativeFilePath", "src/App.vue"),
            () -> { executions.incrementAndGet(); return Map.of("content", "ready"); });
    Map<String, Object> replay = service.execute(
            42L, "req-1", "call-1", InternalAiTool.FILE_READ,
            Map.of("relativeFilePath", "src/App.vue"),
            () -> { executions.incrementAndGet(); return Map.of("content", "wrong"); });
    assertEquals(first, replay);
    assertEquals(1, executions.get());
}
```

Add tests for:

- key `ai:tool:idempotency:v1:42:req-1:call-1`;
- same scope with a different canonical tool or arguments returns `TOOL_IDEMPOTENCY_CONFLICT`;
- a stored `RUNNING` record returns `TOOL_EXECUTION_INDETERMINATE`;
- lock timeout returns `TOOL_EXECUTION_BUSY`;
- explicit action failure deletes `RUNNING` and rethrows the original exception;
- success writes `SUCCEEDED` with configured TTL;
- Redis lock、读取或 `RUNNING` 占位写入失败时返回 `TOOL_IDEMPOTENCY_UNAVAILABLE`，并且不执行 action；
- action 已成功但 `SUCCEEDED` 写回失败时保留 `RUNNING`，返回 `TOOL_EXECUTION_INDETERMINATE`；
- two service instances sharing the same mocked bucket replay the same result.

- [x] **Step 2: Run the service test and verify it fails**

Run:

```powershell
mvn test -Dtest=ToolInvocationIdempotencyServiceTest
```

Expected: FAIL because `ToolInvocationIdempotencyService` and its properties do not exist.

- [x] **Step 3: Add Redis idempotency configuration**

Add to `AiEngineProperties`:

```java
/** 内部工具成功结果和状态的 Redis 保留时间，单位为秒。 */
private long toolIdempotencyTtlSeconds = 86400;
/** 等待同一工具调用分布式锁的最长时间，单位为毫秒。 */
private long toolIdempotencyLockWaitMillis = 30000;
```

- [x] **Step 4: Implement the service state and fingerprint**

Create a Spring `@Service` with this public boundary:

```java
public Map<String, Object> execute(
        long appId,
        String requestId,
        String toolCallId,
        InternalAiTool tool,
        Map<String, Object> arguments,
        ToolAction action) {
    // acquire scoped lock, inspect state, claim RUNNING, execute, store SUCCEEDED
}

@FunctionalInterface
public interface ToolAction {
    Map<String, Object> execute();
}
```

Use a JSON record with fields `status`, `toolName`, `requestFingerprint`, `result`, `startedAtEpochMillis`, and `completedAtEpochMillis`. Configure a copied `ObjectMapper` with `SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS` and hash:

```text
canonicalToolName + "\n" + canonicalArgumentsJson
```

with SHA-256. Validate `requestId` and `toolCallId` against `[A-Za-z0-9:_-]{1,200}` before forming Redis keys.

Use `RLock.tryLock(lockWaitMillis, TimeUnit.MILLISECONDS)` without a fixed lease time so Redisson's watchdog protects a live execution. Always unlock in `finally` only when held by the current thread.

Store both `RUNNING` and `SUCCEEDED` with `bucket.set(json, ttlSeconds, TimeUnit.SECONDS)`. Delete `RUNNING` only when the action throws an explicit exception. Do not execute the action if lock、state read or `RUNNING` claim fails. If the action returns successfully but storing `SUCCEEDED` fails, preserve `RUNNING` and raise `TOOL_EXECUTION_INDETERMINATE` because the external side effect may already exist.

- [x] **Step 5: Run the service tests**

Run:

```powershell
mvn test -Dtest=ToolInvocationIdempotencyServiceTest
```

Expected: all service tests PASS.

- [x] **Step 6: Commit the Redis service**

```powershell
git add src/main/java/com/yupi/yuaicodemother/config/AiEngineProperties.java `
  src/main/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyService.java
git add -f src/test/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyServiceTest.java
git commit -m "feat: 增加 Redis 工具幂等服务"
```

### Task 3: Integrate Idempotency Into The Spring Tool Controller

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`

- [x] **Step 1: Write failing controller contract tests**

Change `ToolRequest` construction to:

```java
new InternalAiToolsController.ToolRequest(
        42L,
        "req-1",
        "call-1",
        "file_read",
        Map.of("codeGenType", "VUE_PROJECT", "relativeFilePath", "src/App.vue"));
```

Add assertions that:

- blank requestId is rejected before the service is called;
- top-level appId is passed to sandbox resolution and the idempotency service;
- canonical `InternalAiTool` is passed to the service even when the request uses `readFile`;
- `artifact_publish` receives the top-level requestId;
- the old static `ConcurrentHashMap` no longer exists or influences a second controller instance.

- [x] **Step 2: Run controller tests and verify they fail**

Run:

```powershell
mvn test -Dtest=InternalAiToolsControllerTest
```

Expected: FAIL because `ToolRequest` and the controller constructor still use the process-local cache contract.

- [x] **Step 3: Route controller execution through the Redis service**

Replace the request record with:

```java
public record ToolRequest(
        long appId,
        String requestId,
        String toolCallId,
        String toolName,
        Map<String, Object> arguments) { }
```

Remove `IDEMPOTENT_RESULTS`, `readResult`, and the synchronized block. Normalize `toolName` before invoking the service:

```java
InternalAiTool tool = parseTool(request.toolName());
Map<String, Object> arguments = request.arguments() == null ? Map.of() : request.arguments();
Map<String, Object> result = idempotencyService.execute(
        request.appId(), request.requestId(), request.toolCallId(), tool, arguments,
        () -> execute(tool, request.appId(), request.requestId(), arguments));
return ResultUtils.success(result);
```

Change the internal dispatch signature to:

```java
private Map<String, Object> execute(
        InternalAiTool tool,
        long appId,
        String requestId,
        Map<String, Object> args)
```

Pass the top-level requestId to `publishArtifact(long appId, String requestId, Map<String,Object> args)`. All file and build methods use the top-level appId.

- [x] **Step 4: Run Spring tests**

Run:

```powershell
mvn test "-Dtest=InternalAiToolsControllerTest,InternalAiToolContractTest,ToolInvocationIdempotencyServiceTest"
```

Expected: all selected tests PASS.

- [x] **Step 5: Commit the controller integration**

```powershell
git add src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java `
  src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java
git commit -m "refactor: 接入 Redis 工具幂等边界"
```

### Task 4: Document And Verify The Cross-Service Change

**Files:**
- Modify: `src/main/resources/application.yml`
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-startup.md`
- Modify: `doc/ai-service-phase-one-handoff.md`
- Modify: `docs/superpowers/plans/2026-09-21-redis-tool-idempotency.md`

- [x] **Step 1: Document the new configuration and semantics**

Add these Spring properties to `application.yml`:

```yaml
ai:
  tool-idempotency-ttl-seconds: ${AI_TOOL_IDEMPOTENCY_TTL_SECONDS:86400}
  tool-idempotency-lock-wait-millis: ${AI_TOOL_IDEMPOTENCY_LOCK_WAIT_MILLIS:30000}
```

Document their environment-variable form in the README and startup guide:

```dotenv
# Spring 内部工具幂等记录的保留时间，单位为秒
AI_TOOL_IDEMPOTENCY_TTL_SECONDS=86400
# Spring 等待同一工具调用分布式锁的最长时间，单位为毫秒
AI_TOOL_IDEMPOTENCY_LOCK_WAIT_MILLIS=30000
```

Update the README, startup guide, and handoff document to state:

- results are shared through Spring Redis database 1;
- scope is `appId + requestId + toolCallId`;
- same ID with different payload is rejected;
- stale `RUNNING` is indeterminate and is not automatically replayed;
- Redis idempotency and versioned artifact publication remain separate mechanisms;
- real Redis multi-instance verification remains an integration-test requirement unless run during this task.

- [x] **Step 2: Run Python verification**

Run:

```powershell
cd ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Expected: all commands exit 0.

Actual: `compileall` and `uv lock --check` exited 0; final full `pytest` passed 65 tests with 2 dependency deprecation warnings.

- [x] **Step 3: Run Java verification**

Run from the repository root:

```powershell
mvn test "-Dtest=InternalAiToolsControllerTest,InternalAiToolContractTest,ToolInvocationIdempotencyServiceTest"
mvn clean -DskipTests compile
```

Expected: selected tests pass and compile reports `BUILD SUCCESS`.

Actual: 36 selected tests passed with no failures, errors, or skips; clean compile built 234 source files and reported `BUILD SUCCESS`.

- [x] **Step 4: Verify repository hygiene and plan coverage**

Run:

```powershell
git diff --check
git status --short
rg -n "ConcurrentHashMap|IDEMPOTENT_RESULTS" src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java
```

Expected: no whitespace errors, only task files are changed, and the final search returns no process-local idempotency cache.

- [x] **Step 5: Commit documentation and completed plan state**

```powershell
git add src/main/resources/application.yml ai-service/README.md `
  doc/ai-service-startup.md doc/ai-service-phase-one-handoff.md `
  docs/superpowers/plans/2026-09-21-redis-tool-idempotency.md
git commit -m "docs: 记录 Redis 工具幂等契约"
```
