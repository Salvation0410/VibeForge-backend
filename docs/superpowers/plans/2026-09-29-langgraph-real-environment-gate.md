# LangGraph Real Environment Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 LangGraph 真实网络阻断并建立可重复执行的 Spring/Python、认证、Redis 幂等、三类型生成、取消和 Legacy 回滚验收入口。

**Architecture:** Spring 继续通过统一 `AiGenerationGateway` 调用 Python，Python 继续通过受控工具网关访问 Spring。本轮只修复真实 HTTP 协议和验收工具，不改变公开 SSE、文件所有权、发布状态机或灰度算法。

**Tech Stack:** Java 21、Spring Boot、JDK HttpClient、JUnit 5、PowerShell、Redis/Redisson、FastAPI/Uvicorn。

---

### Task 1: Pin the LangGraph client to HTTP/1.1

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/gateway/LangGraphAiGenerationGateway.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/ai/gateway/LangGraphAiGenerationGatewayTest.java`

- [x] Add a local HTTP server regression test that records the request protocol and `Upgrade` header.
- [x] Run `mvn "-Dtest=LangGraphAiGenerationGatewayTest" test` and confirm the new assertion fails on the default JDK client.
- [x] Configure the shared client with `HttpClient.Version.HTTP_1_1`.
- [x] Rerun the gateway test and confirm route and NDJSON tests pass.

### Task 2: Align the Spring tool validation script with BaseResponse

**Files:**
- Modify: `scripts/test-ai-service.ps1`
- Modify: `scripts/ai-validation-scripts.tests.ps1`

- [x] Add static assertions requiring HTTP 200 plus business codes `40101` and `40000`.
- [x] Run `pwsh -NoProfile -File scripts/ai-validation-scripts.tests.ps1` and confirm it fails against the old script.
- [x] Update missing-token, invalid-token, and missing-field checks to assert both transport status and business code.
- [x] Keep result reports free of tokens, request bodies, response bodies, and source content.

### Task 3: Support captcha login and controlled authenticated sessions

**Files:**
- Create: `scripts/ai-validation-auth.ps1`
- Modify: `scripts/compare-ai-generation-engines.ps1`
- Modify: `scripts/test-ai-phase-two-e2e.ps1`
- Modify: `scripts/ai-validation-scripts.tests.ps1`

- [x] Add static tests requiring an authenticated-session parameter and captcha endpoint support.
- [x] Implement a shared login helper that either imports a caller-provided Cookie header or fetches a captcha in the same session and prompts for its code.
- [x] Ensure temporary captcha files are deleted in `finally` and credentials are never printed or written to reports.
- [x] Preserve dry-run behavior: no login, captcha, model, or application request without `-Execute`.

### Task 4: Extend real Redis multi-client competition coverage

**Files:**
- Modify: `src/test/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyRedisIT.java`

- [x] Add a two-client concurrent same-request test proving the action executes once and both callers receive the same result.
- [x] Add same-scope parameter-conflict and canonical-tool-conflict tests proving the second action never executes.
- [x] Run the test with `AI_REDIS_INTEGRATION=true` when Redis is reachable; 6 tests passed against `redis://localhost:6379/1`.

### Task 5: Verify the real-environment scripts and service boundaries

**Files:**
- Modify only if failures identify a repository defect.

- [x] Run the Java gateway, idempotency, HTTP contract, controller, lease, and cancellation tests; 71 tests passed.
- [x] Run PowerShell script static tests with PowerShell 7 and load the authentication helper with Windows PowerShell 5.1.
- [x] Python source and contracts were not changed, so Python verification was not required for this commit.
- [x] Execute the HTTP checklist against Spring 8123 and 8124 plus Python 8000; both reports matched HTTP 200 plus Spring business-code contracts.
- [ ] Execute three-type generation, cancellation, engine comparison, and rollback scripts after the user provides or establishes an authenticated session and isolated application IDs. The local browser and environment currently contain no reusable session Cookie; CAPTCHA completion requires user interaction.
- [x] Record unavailable external prerequisites without claiming the blocked scenarios passed.

### Task 6: Final verification and commit

**Files:**
- Review all changed files.

- [x] Run `mvn clean -DskipTests compile`.
- [x] Run all relevant targeted Java tests and the validation script checks with PowerShell 7 and Windows PowerShell 5.1.
- [x] Run `git diff --check` and confirm no unrelated or generated files are included.
- [x] Commit only the implementation plan, Java source/tests, and validation scripts on `codex/langgraph-real-gate`.
