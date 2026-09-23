# Stable Gray Routing and Validation Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Legacy/LangGraph 灰度路由漂移，并提供默认不执行真实请求的双引擎、HTTP、Redis 和三类型生成验收入口。

**Architecture:** Spring 使用 `graySalt` 和稳定业务主体计算 SHA-256 灰度桶，所有路由、生成和取消操作复用同一选择函数。PowerShell 脚本只负责真实环境的参数校验、请求执行、脱敏摘要和人工检查清单；真实请求必须显式传入 `-Execute`。Redis 故障窗口由默认跳过的 JUnit 集成测试覆盖，现有 Java/Python 单元测试继续承担离线协议验证。

**Tech Stack:** Java 21、Spring Boot 3.5.4、JUnit 5、Mockito、Redisson、PowerShell 5.1+、Python 3.12、pytest、httpx、uv。

---

## File Map

- Modify `src/main/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGateway.java`: 稳定灰度主体、SHA-256 落桶和三入口一致委派。
- Create `src/test/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGatewayTest.java`: 固定引擎、白名单、比例、盐、回退主体及取消一致性测试。
- Create `scripts/compare-ai-generation-engines.ps1`: 对两个已启动且分别配置 Legacy/LangGraph 的 Spring 环境采集脱敏摘要。
- Create `scripts/test-ai-service.ps1`: 真实 Spring/Python 健康与内部工具 HTTP 契约入口。
- Modify `src/test/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyRedisIT.java`: 增加陈旧 RUNNING 和写回失败故障窗口测试。
- Create `scripts/test-ai-phase-two-e2e.ps1`: 三个独立测试应用的首次生成、二次修改、停止和回滚验收入口。
- Create `scripts/ai-validation-scripts.tests.ps1`: 三个 PowerShell 脚本的静态安全门和无执行参数测试。
- Modify `AGENTS.md`: 修正 Redis 工具幂等现状。
- Modify `doc/ai-service-startup.md`: 增加灰度配置和真实验收命令。
- Modify `doc/ai-service-phase-one-handoff.md`: 记录本轮实现与尚未执行的真实验收。
- Modify `ai-service/README.md`: 增加 Spring/Python 契约验收入口说明。

### Task 1: Stable Gray Routing

**Files:**
- Create: `src/test/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGatewayTest.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGateway.java`

- [ ] **Step 1: Write failing tests for stable subjects and consistent delegation**

Create a test fixture with mocked gateways and add focused tests using the public interface. Use a small helper to find a user whose bucket falls on each side of a percentage rather than hard-coding a digest result:

```java
class DelegatingAiGenerationGatewayTest {
    private final AiEngineProperties properties = new AiEngineProperties();
    private final AiGenerationGateway legacy = mock(AiGenerationGateway.class);
    private final AiGenerationGateway langGraph = mock(AiGenerationGateway.class);
    private final DelegatingAiGenerationGateway gateway =
            new DelegatingAiGenerationGateway(properties, legacy, langGraph);

    @Test
    void sameUserUsesSameEngineAcrossRequestIds() {
        properties.setEngine("gray");
        properties.setGraySalt("stable-test");
        properties.setGrayPercentage(50);
        when(legacy.route(anyString(), any(), anyLong(), anyString())).thenReturn(CodeGenTypeEnum.HTML);
        when(langGraph.route(anyString(), any(), anyLong(), anyString())).thenReturn(CodeGenTypeEnum.VUE_PROJECT);

        CodeGenTypeEnum first = gateway.route("build", 1L, 42L, "request-a");
        CodeGenTypeEnum second = gateway.route("build", 2L, 42L, "request-b");

        assertEquals(first, second);
        assertEquals(2, mockingDetails(legacy).getInvocations().size()
                + mockingDetails(langGraph).getInvocations().size());
    }

    @Test
    void routeGenerateAndCancelUseSameSubject() {
        properties.setEngine("gray");
        properties.setGraySalt("consistent-test");
        properties.setGrayPercentage(50);
        when(legacy.route(anyString(), anyLong(), anyLong(), anyString())).thenReturn(CodeGenTypeEnum.HTML);
        when(langGraph.route(anyString(), anyLong(), anyLong(), anyString())).thenReturn(CodeGenTypeEnum.HTML);
        when(legacy.generate(anyString(), any(), anyLong(), anyLong(), anyString())).thenReturn(Flux.empty());
        when(langGraph.generate(anyString(), any(), anyLong(), anyLong(), anyString())).thenReturn(Flux.empty());

        gateway.route("build", 7L, 42L, "req-1");
        gateway.generate("build", CodeGenTypeEnum.HTML, 7L, 42L, "req-1").blockLast();
        gateway.cancel(7L, 42L, "req-1");

        boolean usedLegacy = mockingDetails(legacy).getInvocations().size() == 3;
        boolean usedLangGraph = mockingDetails(langGraph).getInvocations().size() == 3;
        assertTrue(usedLegacy || usedLangGraph);
    }
}
```

Add separate tests for whitelist precedence, percentages `0`, `100`, `-1`, `101`, changed salt, `appId` fallback, `requestId` fallback, fixed `legacy`, fixed `langgraph`, and unknown-engine fallback. For salt testing, iterate salts `salt-0` through `salt-999` until one selects the opposite mocked return value, then assert a different salt can change assignment.

- [ ] **Step 2: Run the gateway test and confirm the current request-based implementation fails**

Run:

```powershell
mvn -Dtest=DelegatingAiGenerationGatewayTest test
```

Expected: at least `sameUserUsesSameEngineAcrossRequestIds` fails because the current bucket includes `requestId`; salt-related tests also fail because `graySalt` is unused.

- [ ] **Step 3: Implement one stable selection function**

Replace the existing delegate calls and hash logic with:

```java
private AiGenerationGateway delegate(Long appId, Long userId, String requestId) {
    String engine = Objects.toString(properties.getEngine(), "legacy").toLowerCase();
    if ("langgraph".equals(engine)) return langGraph;
    if ("gray".equals(engine) || "auto".equals(engine)) {
        if (userId != null && properties.getGrayWhitelist().contains(userId)) return langGraph;
        int percentage = Math.max(0, Math.min(100, properties.getGrayPercentage()));
        if (percentage > 0 && stableBucket(appId, userId, requestId) < percentage) return langGraph;
    }
    return legacy;
}

private int stableBucket(Long appId, Long userId, String requestId) {
    String subject = userId != null ? "user:" + userId
            : appId != null ? "app:" + appId
            : "request:" + Objects.toString(requestId, "");
    String saltedSubject = Objects.toString(properties.getGraySalt(), "") + ":" + subject;
    try {
        byte[] digest = MessageDigest.getInstance("SHA-256")
                .digest(saltedSubject.getBytes(StandardCharsets.UTF_8));
        return (ByteBuffer.wrap(digest, 0, Integer.BYTES).getInt() & Integer.MAX_VALUE) % 100;
    } catch (NoSuchAlgorithmException error) {
        throw new IllegalStateException("SHA-256 is unavailable", error);
    }
}
```

Update all public methods to call `delegate(appId, userId, requestId)`. Add imports for `ByteBuffer`, `StandardCharsets`, `MessageDigest`, and `NoSuchAlgorithmException`. Update `AiEngineProperties.graySalt` comment from “预留” to its active purpose.

- [ ] **Step 4: Run focused gateway tests**

Run:

```powershell
mvn -Dtest=DelegatingAiGenerationGatewayTest,LangGraphAiGenerationGatewayTest test
```

Expected: both test classes pass; the NDJSON adapter behavior remains unchanged.

- [ ] **Step 5: Commit stable routing**

```powershell
git add -- src/main/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGateway.java src/main/java/com/yupi/yuaicodemother/config/AiEngineProperties.java src/test/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGatewayTest.java
git commit -m "fix: 稳定 AI 引擎灰度路由"
```

### Task 2: Safe Script Test Harness

**Files:**
- Create: `scripts/ai-validation-scripts.tests.ps1`
- Create: `scripts/compare-ai-generation-engines.ps1`
- Create: `scripts/test-ai-service.ps1`
- Create: `scripts/test-ai-phase-two-e2e.ps1`

- [ ] **Step 1: Write static safety tests before the scripts exist**

Create a dependency-free PowerShell test that reads each script source and checks the common guardrails:

```powershell
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$expectedScripts = @(
    'compare-ai-generation-engines.ps1',
    'test-ai-service.ps1',
    'test-ai-phase-two-e2e.ps1'
)

foreach ($name in $expectedScripts) {
    $path = Join-Path $PSScriptRoot $name
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing validation script: $name" }
    $source = Get-Content -Raw -LiteralPath $path
    if ($source -notmatch '\[switch\]\$Execute') { throw "$name must require -Execute" }
    if ($source -notmatch 'Set-StrictMode -Version Latest') { throw "$name must enable strict mode" }
    if ($source -notmatch '\$ErrorActionPreference\s*=\s*["'']Stop["'']') { throw "$name must stop on errors" }
    if ($source -match 'Write-(Host|Output).*\$.*(Token|Password|Cookie)') {
        throw "$name may expose credentials"
    }
}

'AI validation script static checks passed'
```

- [ ] **Step 2: Run the safety test and verify it fails on missing scripts**

Run:

```powershell
powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1
```

Expected: FAIL with `Missing validation script: compare-ai-generation-engines.ps1`.

- [ ] **Step 3: Create minimal guarded script shells**

Each script must begin with a parameter block containing `[switch]$Execute`, followed by:

```powershell
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if (-not $Execute) {
    Write-Host 'Dry run only. Review the checklist and rerun with -Execute to send requests.'
    return
}
```

The comparison and E2E scripts must validate all application IDs are positive and distinct before the first `Invoke-WebRequest`. The HTTP script must require non-empty `InternalToken` and positive `ReadOnlyAppId`. Do not include default credentials or tokens.

- [ ] **Step 4: Run static and dry-run tests**

Run:

```powershell
powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1
powershell -NoProfile -File scripts/compare-ai-generation-engines.ps1
powershell -NoProfile -File scripts/test-ai-service.ps1
powershell -NoProfile -File scripts/test-ai-phase-two-e2e.ps1
```

Expected: static checks pass; all three scripts exit without network access and print their dry-run notice.

- [ ] **Step 5: Commit the guarded shells and tests**

```powershell
git add -- scripts/ai-validation-scripts.tests.ps1 scripts/compare-ai-generation-engines.ps1 scripts/test-ai-service.ps1 scripts/test-ai-phase-two-e2e.ps1
git commit -m "test: 增加 AI 验收脚本安全门"
```

### Task 3: Legacy/LangGraph Summary Comparison

**Files:**
- Modify: `scripts/compare-ai-generation-engines.ps1`
- Modify: `scripts/ai-validation-scripts.tests.ps1`

- [ ] **Step 1: Add failing source-contract tests for report privacy**

Extend the PowerShell test to require the allowed report fields and reject dangerous output fields:

```powershell
$comparison = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot 'compare-ai-generation-engines.ps1')
$allowed = @('engine','appId','codeGenType','requestId','terminalStatus','toolNames','artifactHashes','buildStatus','errorCode','durationMs')
foreach ($field in $allowed) {
    if ($comparison -notmatch [regex]::Escape($field)) { throw "Comparison report misses $field" }
}
foreach ($forbidden in @('prompt =','source =','cookie =','token =','toolArguments =')) {
    if ($comparison -match [regex]::Escape($forbidden)) { throw "Comparison report contains forbidden field $forbidden" }
}
```

- [ ] **Step 2: Run the script test and confirm it fails on missing report fields**

Run `powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1`.

Expected: FAIL with `Comparison report misses engine`.

- [ ] **Step 3: Implement login, SSE parsing and sanitized report output**

Implement three focused helpers inside the script. `New-AuthenticatedSession` creates a `WebRequestSession`, posts `{account,password}` to `/users/login`, requires `code=0`, and returns only the session. `Invoke-GenerationStream` URL-encodes `appId` and `message`, calls `/apps/chat/gen/code` with that session, retains only parsed SSE event names and metadata, and never retains content chunks. `ConvertTo-GenerationSummary` accepts engine, app ID, generation type, parsed events, and duration, rejects zero or multiple terminal events, and returns the explicit ordered object below.

Use `System.Diagnostics.Stopwatch` for duration. Parse `event: done`, `event: business-error`, and `data:` records without writing content chunks. Hash any artifact identifier returned by a permitted result with SHA-256; never persist artifact text. Require separate `LegacyBaseUrl`, `LangGraphBaseUrl`, `LegacyAppId`, `LangGraphAppId`, account/password inputs, and three explicit prompts. Write JSON with `ConvertTo-Json -Depth 8` under `target/ai-validation/compare-<timestamp>.json` unless `OutputPath` is supplied.

The output object must be constructed explicitly:

```powershell
[ordered]@{
    engine = $Engine
    appId = $AppId
    codeGenType = $CodeGenType
    requestId = $requestId
    terminalStatus = $terminalStatus
    toolNames = @($toolNames | Sort-Object -Unique)
    artifactHashes = @($artifactHashes | Sort-Object -Unique)
    buildStatus = $buildStatus
    errorCode = $errorCode
    durationMs = $DurationMs
}
```

- [ ] **Step 4: Verify syntax, privacy contract and dry-run behavior**

Run:

```powershell
powershell -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw scripts/compare-ai-generation-engines.ps1))"
powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1
powershell -NoProfile -File scripts/compare-ai-generation-engines.ps1
```

Expected: all commands exit 0; no report or network request is created without `-Execute`.

- [ ] **Step 5: Commit the comparison tool**

```powershell
git add -- scripts/compare-ai-generation-engines.ps1 scripts/ai-validation-scripts.tests.ps1
git commit -m "feat: 增加双引擎摘要对比工具"
```

### Task 4: Spring/Python HTTP Validation Entry Point

**Files:**
- Modify: `scripts/test-ai-service.ps1`
- Modify: `scripts/ai-validation-scripts.tests.ps1`
- Modify: `ai-service/tests/test_gateway_and_config.py`

- [ ] **Step 1: Add failing tests for the real HTTP checklist and Python timeout contract**

Extend the script test to require labels for `health/live`, `health/ready`, missing token, invalid token, valid invocation, missing fields, stable idempotency errors, and sanitization. In Python, add a MockTransport test that captures `request.extensions["timeout"]["read"]` for `project_build` and asserts it is `1100.0`, while a `file_read` call uses `30.0`.

```python
@pytest.mark.asyncio
async def test_project_build_uses_long_read_timeout():
    seen: list[float] = []
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json={"code": 0, "data": {"built": True}, "message": "ok"})
    gateway = SpringToolGateway(base_url="http://spring/internal/ai-tools", bearer_token="token",
                                transport=httpx.MockTransport(handler))
    try:
        await gateway.invoke("project_build", {"codeGenType": "VUE_PROJECT"},
                             app_id="1", request_id="req", tool_call_id="call")
    finally:
        await gateway.close()
    assert seen == [1100.0]
```

- [ ] **Step 2: Run the focused tests and observe the missing script contract**

Run:

```powershell
powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1
Set-Location ai-service
uv run pytest tests/test_gateway_and_config.py -q
Set-Location ..
```

Expected: the PowerShell contract fails until labels are implemented; the existing Python long-timeout behavior should pass and becomes regression coverage.

- [ ] **Step 3: Implement the real HTTP validation flow**

Add helpers that return status, parsed JSON, and a redacted message. Execute in this order only after all parameters pass validation:

```text
GET  Python /health/live
GET  Python /health/ready
POST Spring /api/internal/ai-tools/invoke without Authorization
POST Spring /api/internal/ai-tools/invoke with an invalid token
POST Spring /api/internal/ai-tools/invoke with the valid token and artifact_context
POST Spring /api/internal/ai-tools/invoke with each required field omitted
```

Use unique `requestId` and `toolCallId` values. The successful call must be read-only:

```powershell
$body = @{
    appId = $ReadOnlyAppId
    requestId = "http-contract-$([guid]::NewGuid().ToString('N'))"
    toolCallId = 'artifact-context'
    toolName = 'artifact_context'
    arguments = @{ codeGenType = $CodeGenType }
}
```

Do not attempt to manufacture malformed Spring responses. Print that malformed JSON, wrong field types, missing data, stable error preservation, and timeout selection are covered by offline tests. If `VueBuildAppId` is supplied, perform `project_build` only after an additional `-IncludeBuild` switch.

- [ ] **Step 4: Verify offline HTTP contracts and dry run**

Run:

```powershell
powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1
powershell -NoProfile -File scripts/test-ai-service.ps1
Set-Location ai-service
uv run pytest tests/test_gateway_and_config.py -q
Set-Location ..
```

Expected: all pass without contacting Spring or Python.

- [ ] **Step 5: Commit the HTTP validation entry point**

```powershell
git add -- scripts/test-ai-service.ps1 scripts/ai-validation-scripts.tests.ps1 ai-service/tests/test_gateway_and_config.py
git commit -m "test: 增加 Spring Python HTTP 验收入口"
```

### Task 5: Redis Indeterminate-State Integration Coverage

**Files:**
- Modify: `src/test/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyRedisIT.java`
- Test: `src/test/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyServiceTest.java`

- [ ] **Step 1: Add a package-private test seam for key calculation only if required**

Prefer constructing the Redis key in the integration test with the same documented encoding for generated alphanumeric IDs. If production access is required, extract only this package-private method without changing behavior:

```java
String stateKey(long appId, String requestId, String toolCallId) {
    return KEY_PREFIX + appId + ":" + encodeKeyComponent(requestId)
            + ":" + encodeKeyComponent(toolCallId);
}
```

Keep fingerprinting and state parsing private.

- [ ] **Step 2: Write opt-in tests for stale RUNNING and post-action write failure**

Use unique alphanumeric IDs, write a valid RUNNING JSON record through the first Redis client, and call through the second service:

```java
@Test
void staleRunningStateIsIndeterminateAndDoesNotExecuteAction() {
    String requestId = unique("running");
    String toolCallId = unique("call");
    String key = "ai:tool:idempotency:v1:42:" + requestId + ":" + toolCallId;
    String fingerprint = sha256("file_read\n{\"relativeFilePath\":\"src/App.vue\"}");
    firstClient.getBucket(key).set("{\"status\":\"RUNNING\",\"toolName\":\"file_read\"," +
            "\"requestFingerprint\":\"" + fingerprint + "\",\"result\":null," +
            "\"startedAtEpochMillis\":1,\"completedAtEpochMillis\":null}", 60, TimeUnit.SECONDS);
    AtomicInteger executions = new AtomicInteger();

    BusinessException error = assertThrows(BusinessException.class, () -> second.execute(
            42L, requestId, toolCallId, InternalAiTool.FILE_READ,
            Map.of("relativeFilePath", "src/App.vue"),
            () -> { executions.incrementAndGet(); return Map.of(); }));

    assertEquals("TOOL_EXECUTION_INDETERMINATE", error.getMessage());
    assertEquals(0, executions.get());
}
```

For the post-action write failure, create a third Redisson client, start `execute`, wait until the action runs, shut that client down before the SUCCEEDED write, and assert `TOOL_EXECUTION_INDETERMINATE`. Reconnect a client, retry the same scope, and assert the action count remains one because RUNNING persists. Use latches and bounded waits; always release and close clients in `finally`.

- [ ] **Step 3: Compile and verify the integration class remains skipped by default**

Run:

```powershell
mvn -Dtest=ToolInvocationIdempotencyRedisIT test
mvn -Dtest=ToolInvocationIdempotencyServiceTest test
```

Expected: integration class reports skipped when `AI_REDIS_INTEGRATION` is unset; unit tests pass. Do not set the environment variable in this round.

- [ ] **Step 4: Commit Redis fault coverage**

```powershell
git add -- src/main/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyService.java src/test/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyRedisIT.java
git commit -m "test: 覆盖 Redis 幂等不确定状态"
```

Omit the production service from `git add` if no package-private seam was needed.

### Task 6: Three-Type End-to-End Acceptance Script

**Files:**
- Modify: `scripts/test-ai-phase-two-e2e.ps1`
- Modify: `scripts/ai-validation-scripts.tests.ps1`

- [ ] **Step 1: Add failing safety and checklist assertions**

Require source markers for three distinct positive app IDs, `-Execute`, first-generation prompts, second-modification prompts, `done`, `business-error`, stop/disconnect, old-version retention, and a manual review section. Add a duplicate-ID rejection dry invocation:

```powershell
$result = & powershell -NoProfile -File (Join-Path $PSScriptRoot 'test-ai-phase-two-e2e.ps1') `
    -Execute -HtmlAppId 1 -MultiFileAppId 1 -VueAppId 2 2>&1
if ($LASTEXITCODE -eq 0 -or "$result" -notmatch 'must be distinct') {
    throw 'E2E script must reject duplicate application IDs before network access'
}
```

- [ ] **Step 2: Run the test and verify checklist assertions fail**

Run `powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1`.

Expected: FAIL on the first missing E2E marker.

- [ ] **Step 3: Implement bounded SSE collection and per-application scenarios**

Reuse local helpers within the script; do not dot-source the comparison script because both scripts must remain independently understandable. For each type, run an initial prompt followed by a narrowly scoped modification prompt. Parse SSE into a summary only:

```powershell
[ordered]@{
    codeGenType = $CodeGenType
    appId = $AppId
    phase = $Phase
    terminalStatus = $TerminalStatus
    doneCount = $DoneCount
    businessErrorCode = $BusinessErrorCode
    durationMs = $DurationMs
    manualChecks = @(
        '未指定的文字、图片、功能和操作方式仍然保留',
        '预览只在当前请求成功后刷新一次'
    )
}
```

Use a configurable timeout and stop reading after exactly one terminal event. Fail automatically on zero/multiple terminal events, `business-error` in a success scenario, or mismatched application metadata. Include separate opt-in `-RunCancellationScenario` and `-RunLegacyRollbackScenario` switches because stopping a live stream and using a separately configured Legacy service require explicit operator intent.

Do not claim Selenium, publication retention, visual correctness, or semantic preservation solely from SSE. Print the exact manual checks and relevant preview/download/history URLs for the operator.

- [ ] **Step 4: Run syntax, static safety, duplicate-ID and dry-run tests**

Run:

```powershell
powershell -NoProfile -Command "[void][scriptblock]::Create((Get-Content -Raw scripts/test-ai-phase-two-e2e.ps1))"
powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1
powershell -NoProfile -File scripts/test-ai-phase-two-e2e.ps1
```

Expected: all commands pass without sending a generation request.

- [ ] **Step 5: Commit the E2E acceptance script**

```powershell
git add -- scripts/test-ai-phase-two-e2e.ps1 scripts/ai-validation-scripts.tests.ps1
git commit -m "test: 增加三类型生成验收入口"
```

### Task 7: Documentation and Offline Verification

**Files:**
- Modify: `AGENTS.md`
- Modify: `doc/ai-service-startup.md`
- Modify: `doc/ai-service-phase-one-handoff.md`
- Modify: `ai-service/README.md`

- [ ] **Step 1: Update documentation without overwriting existing user edits**

Edit only the relevant paragraphs. In `AGENTS.md`, replace the obsolete process-local statement with this factual boundary:

```markdown
内部工具幂等状态和成功结果通过 Spring Redisson 保存在 Redis，作用域为
`appId + requestId + toolCallId`，支持跨 Spring 实例共享；文件系统操作与 Redis
状态写入并非同一事务，因此 `RUNNING` 和写回失败仍按不确定状态处理，不承诺严格 exactly-once。
```

Document the stable subject priority, `graySalt`, both-service comparison command, HTTP validation command, Redis opt-in command, and E2E command. Every section must explicitly state that this round did not start services or execute real model/Redis/E2E requests.

- [ ] **Step 2: Run documentation consistency searches**

Run:

```powershell
rg -n "ConcurrentHashMap|进程内.*幂等|graySalt|AI_REDIS_INTEGRATION|test-ai-phase-two-e2e|compare-ai-generation-engines" AGENTS.md ai-service/README.md doc/ai-service-startup.md doc/ai-service-phase-one-handoff.md
```

Expected: no statement claims tool idempotency is still process-local; commands and pending real-environment status are discoverable.

- [ ] **Step 3: Run Java verification**

Run:

```powershell
mvn -Dtest=DelegatingAiGenerationGatewayTest,LangGraphAiGenerationGatewayTest,ToolInvocationIdempotencyServiceTest,InternalAiToolsHttpContractTest test
mvn -Dtest=ToolInvocationIdempotencyRedisIT test
mvn clean -DskipTests compile
```

Expected: focused tests and clean compile pass; Redis integration test is skipped because `AI_REDIS_INTEGRATION` is unset.

- [ ] **Step 4: Run Python verification**

Run:

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
Set-Location ..
```

Expected: all Python tests pass; existing dependency warnings may remain but no new failures appear.

- [ ] **Step 5: Run script and repository hygiene checks**

Run:

```powershell
powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1
powershell -NoProfile -File scripts/compare-ai-generation-engines.ps1
powershell -NoProfile -File scripts/test-ai-service.ps1
powershell -NoProfile -File scripts/test-ai-phase-two-e2e.ps1
git diff --check
git status --short
git -C D:/VibeForge/yu-ai-code-mother-frontend status --short
```

Expected: script checks and dry runs pass; no real requests occur; whitespace check passes; existing user modifications remain visible and are not reverted or included accidentally.

- [ ] **Step 6: Commit only task documentation**

Before staging, inspect `git diff` for each documentation file and preserve the user's existing edits in `doc/ai-service-phase-one-handoff.md`. Then run:

```powershell
git add -- AGENTS.md doc/ai-service-startup.md doc/ai-service-phase-one-handoff.md ai-service/README.md
git diff --cached --check
git commit -m "docs: 记录稳定灰度与验收流程"
```

- [ ] **Step 7: Record the handoff evidence**

The final delivery must state:

```text
已执行：Java focused tests、Java clean compile、Python compileall/pytest/lock check、PowerShell static and dry-run tests。
未执行：真实 Spring/Python 网络调用、真实 Redis 断言、真实 DeepSeek、三类型端到端生成、灰度比例调整。
下一步：由用户在已启动的隔离测试环境中使用 -Execute 和三个独立测试应用 ID 运行验收脚本。
```

Do not claim full production readiness until the pending real-environment checks pass.
