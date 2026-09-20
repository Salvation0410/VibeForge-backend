# AI Multi-File Safe Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent truncated multi-file model responses from replacing working applications, and make `done` mean that a validated immutable version has been published successfully.

**Architecture:** Spring owns a reusable strict parser, deterministic validator, immutable release publisher, active-version resolver, and per-app generation lease. Legacy calls the pipeline in-process with LangChain4j completion metadata; Python LangGraph calls the same validation and publication boundary through the authenticated Spring tool gateway. Public SSE content remains compatible, while terminal success and failure become explicit.

**Tech Stack:** Java 21, Spring Boot 3.5.4, Reactor, LangChain4j 1.1.0, Redisson, JUnit 5, FastAPI, LangChain, LangGraph, pytest, Vue 3, TypeScript

**Comment requirement:** Every newly added or materially changed core method must have concise Chinese Javadoc/docstrings explaining its responsibility, parameters or return value where useful, and important failure behavior. Do not rewrite unrelated historical comments.

---

### Task 1: Replace fuzzy multi-file parsing with a strict protocol parser

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/core/paser/MultiFileCodeParser.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/core/CodeParserTest.java`
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactValidationException.java`

- [x] **Step 1: Add failing parser regression tests**

Add tests for a missing CSS closing fence, missing JavaScript fence, duplicate file section, explanatory text outside fences, and the incident shape where HTML references `style.css`/`script.js` before a truncated CSS fence. Assert `ArtifactValidationException` with code `MULTI_FILE_FORMAT_INVALID`. Replace the old inline-assets success test with a rejection test because inline extraction is outside the strict protocol.

- [x] **Step 2: Run the parser tests and verify the new cases fail**

Run: `mvn -q -Dtest=CodeParserTest test`

Expected: the newly added malformed-response tests fail against the fuzzy parser.

- [x] **Step 3: Implement the strict parser**

Use one anchored pattern for the exact three-section grammar. The parser must reject blank input, extra non-whitespace text, missing/duplicate/out-of-order sections, unclosed fences, and blank bodies. Remove generic fence and inline asset fallbacks. Throw:

```java
throw new ArtifactValidationException(
        "MULTI_FILE_FORMAT_INVALID",
        null,
        "多文件响应必须包含完整且唯一的 index.html、style.css 和 script.js 区块"
);
```

Add Chinese Javadoc to `parseCode` and the protocol-matching helper.

- [x] **Step 4: Run parser tests**

Run: `mvn -q -Dtest=CodeParserTest test`

Expected: all parser tests pass, including the incident regression.

- [x] **Step 5: Commit the parser change**

```powershell
git add src/main/java/com/yupi/yuaicodemother/core/paser/MultiFileCodeParser.java src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactValidationException.java src/test/java/com/yupi/yuaicodemother/core/CodeParserTest.java
git commit -m "fix: 严格解析多文件生成响应"
```

### Task 2: Add deterministic artifact validation

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactValidationError.java`
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactValidationResult.java`
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/MultiFileArtifactValidator.java`
- Create: `src/test/java/com/yupi/yuaicodemother/core/artifact/MultiFileArtifactValidatorTest.java`

- [x] **Step 1: Add failing validator tests**

Cover valid content and rejection of missing HTML structure, missing stylesheet/script references, inline style/script, unbalanced CSS braces, filename-only CSS/JS, Markdown residue, and unbalanced JavaScript delimiters. Assert stable per-file error codes.

- [x] **Step 2: Run the validator test and verify it fails**

Run: `mvn -q -Dtest=MultiFileArtifactValidatorTest test`

Expected: compilation fails because the validator types do not exist.

- [x] **Step 3: Implement validator records and service**

Expose:

```java
public ArtifactValidationResult validate(MultiFileCodeResult artifact)
public void validateOrThrow(MultiFileCodeResult artifact)
```

The implementation must return all deterministic errors in one pass. Delimiter scanning must ignore quoted strings and CSS comments rather than relying only on raw character counts. Add Chinese Javadoc to both public methods and short Chinese comments before the stateful scanners.

- [x] **Step 4: Run parser and validator tests**

Run: `mvn -q -Dtest=CodeParserTest,MultiFileArtifactValidatorTest test`

Expected: all tests pass.

- [x] **Step 5: Commit deterministic validation**

```powershell
git add src/main/java/com/yupi/yuaicodemother/core/artifact src/test/java/com/yupi/yuaicodemother/core/artifact
git commit -m "feat: 增加多文件产物确定性校验"
```

### Task 3: Add immutable release publication and active path resolution

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactManifest.java`
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPublishResult.java`
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPathResolver.java`
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPublicationService.java`
- Create: `src/test/java/com/yupi/yuaicodemother/core/artifact/ArtifactPublicationServiceTest.java`

- [x] **Step 1: Add failing publication tests**

Use a JUnit temporary directory and injectable output root. Cover first publish, `.current` resolution, same-request idempotency, same-request hash conflict, failed pointer replacement preserving the old version, legacy flat-directory fallback, corrupt-pointer fallback, and retention of current plus two previous valid releases.

- [x] **Step 2: Run the publication tests and verify they fail**

Run: `mvn -q -Dtest=ArtifactPublicationServiceTest test`

Expected: compilation fails because publication classes do not exist.

- [x] **Step 3: Implement the versioned publisher**

Expose:

```java
public ArtifactPublishResult publishMultiFile(
        long appId,
        String requestId,
        String rawArtifact,
        String engine,
        String finishReason)

public Path resolveActiveRoot(CodeGenTypeEnum codeGenType, long appId)
public Path resolveDirectoryName(String directoryName)
```

Write to `.staging/<requestId>`, re-read and hash all three files, atomically move the staging directory to `.releases/<requestId>`, then atomically replace `.current`. Treat an existing matching manifest as idempotent and an existing mismatched manifest as `ARTIFACT_VERSION_CONFLICT`. Never delete the active release. Add Chinese Javadoc to all public methods and to cleanup/fallback helpers whose failure semantics are non-obvious.

- [x] **Step 4: Run artifact tests**

Run: `mvn -q -Dtest=CodeParserTest,MultiFileArtifactValidatorTest,ArtifactPublicationServiceTest test`

Expected: all tests pass.

- [x] **Step 5: Commit version publication**

```powershell
git add src/main/java/com/yupi/yuaicodemother/core/artifact src/test/java/com/yupi/yuaicodemother/core/artifact
git commit -m "feat: 增加多文件版本化原子发布"
```

### Task 4: Route all multi-file readers through the active-version resolver

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/StaticResourceController.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/AppController.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/service/impl/ChatHistoryExportServiceImpl.java`
- Create: `src/test/java/com/yupi/yuaicodemother/core/artifact/ArtifactPathResolverTest.java`

- [x] **Step 1: Add resolver compatibility tests**

Verify that MULTI_FILE resolves `.current`, legacy roots resolve without a pointer, HTML/Vue retain direct roots, and traversal or malformed directory names are rejected.

- [x] **Step 2: Run resolver tests and verify missing integrations**

Run: `mvn -q -Dtest=ArtifactPathResolverTest test`

Expected: tests that exercise current-version resolution fail until the consumers use the resolver.

- [x] **Step 3: Inject and use `ArtifactPathResolver`**

Replace direct multi-file path construction in preview, deployment, download, export, and internal file reads. Keep Vue builder behavior unchanged. Add Chinese comments to each modified resolver-facing method explaining that it resolves the committed version rather than the mutable application root.

- [x] **Step 4: Run artifact and affected service tests**

Run: `mvn -q -Dtest=ArtifactPathResolverTest,ArtifactPublicationServiceTest,AppServicePublicPageTest test`

Expected: all tests pass.

- [x] **Step 5: Commit reader migration**

```powershell
git add src/main/java/com/yupi/yuaicodemother/controller src/main/java/com/yupi/yuaicodemother/service/impl src/test/java/com/yupi/yuaicodemother/core/artifact/ArtifactPathResolverTest.java
git commit -m "refactor: 统一解析当前产物版本"
```

### Task 5: Add per-app generation exclusion

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/ai/gateway/GenerationLease.java`
- Create: `src/main/java/com/yupi/yuaicodemother/ai/gateway/GenerationLeaseService.java`
- Create: `src/test/java/com/yupi/yuaicodemother/ai/gateway/GenerationLeaseServiceTest.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`

- [x] **Step 1: Add failing lease tests**

Mock Redisson and verify same-app rejection, different-app independence, release on complete/error/cancel, and owner-safe unlock. The returned lease must retain the Redisson owner thread ID so a reactive terminal callback can call `unlockAsync(ownerThreadId)` safely.

- [x] **Step 2: Run lease tests and verify they fail**

Run: `mvn -q -Dtest=GenerationLeaseServiceTest test`

Expected: compilation fails because lease classes do not exist.

- [x] **Step 3: Implement and integrate the lease**

Expose:

```java
public GenerationLease acquire(long appId, String requestId)
public void release(GenerationLease lease)
```

Acquire a Redisson `RLock` without a fixed lease time so the watchdog renews it. Store the acquisition thread ID and release with `unlockAsync(ownerThreadId)`. Wrap gateway creation in `Flux.defer` and release exactly once in `doFinally`. Throw a stable `GENERATION_IN_PROGRESS` exception when acquisition fails. Add Chinese Javadoc to acquisition, release, and reactive integration methods.

- [x] **Step 4: Run lease and application service tests**

Run: `mvn -q -Dtest=GenerationLeaseServiceTest,AppServicePublicPageTest test`

Expected: all tests pass.

- [x] **Step 5: Commit generation exclusion**

```powershell
git add src/main/java/com/yupi/yuaicodemother/ai/gateway src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java src/test/java/com/yupi/yuaicodemother/ai/gateway
git commit -m "feat: 防止同一应用并发生成"
```

### Task 6: Make Legacy completion metadata and publication part of the main stream

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/AiCodeGeneratorService.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/core/AiCodeGeneratorFacade.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/core/handler/SimpleTextStreamHandler.java`
- Create: `src/test/java/com/yupi/yuaicodemother/core/AiCodeGeneratorFacadeTest.java`

- [x] **Step 1: Add failing Legacy stream tests**

Use a fake `TokenStream` to verify `STOP` publishes before completion, `LENGTH` emits `MODEL_OUTPUT_TRUNCATED`, publication failure reaches the Flux error channel, and no failure path completes normally.

- [x] **Step 2: Run the Legacy stream tests and verify they fail**

Run: `mvn -q -Dtest=AiCodeGeneratorFacadeTest test`

Expected: tests fail because HTML/MULTI_FILE currently discard `ChatResponse` and swallow save exceptions.

- [x] **Step 3: Convert simple generation methods to `TokenStream`**

Change the two streaming AI Service methods to return `TokenStream`. Adapt partial responses to the existing Flux content format. In the completion callback, reject `LENGTH` and `CONTENT_FILTER`; for MULTI_FILE call `ArtifactPublicationService.publishMultiFile` before `sink.complete`. Remove full-code logging and the `doOnComplete` save block. Add Chinese Javadoc to the new adapter and finish-reason guard.

- [x] **Step 4: Keep failed candidates out of successful history**

Change `SimpleTextStreamHandler` so only normal completion persists the AI response. On error, write a structured log but do not insert a synthetic AI message into model conversation history. Document this behavior in Chinese above the terminal callbacks.

- [x] **Step 5: Run Legacy tests**

Run: `mvn -q -Dtest=AiCodeGeneratorFacadeTest,CodeParserTest,MultiFileArtifactValidatorTest,ArtifactPublicationServiceTest test`

Expected: all tests pass.

- [x] **Step 6: Commit Legacy integration**

```powershell
git add src/main/java/com/yupi/yuaicodemother/ai/AiCodeGeneratorService.java src/main/java/com/yupi/yuaicodemother/core/AiCodeGeneratorFacade.java src/main/java/com/yupi/yuaicodemother/core/handler/SimpleTextStreamHandler.java src/test/java/com/yupi/yuaicodemother/core/AiCodeGeneratorFacadeTest.java
git commit -m "fix: 在 Legacy 完成链路发布有效产物"
```

### Task 7: Expose validation and publication through the Spring tool gateway

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`
- Create: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`

- [x] **Step 1: Add failing controller contract tests**

Cover structured validation errors, successful validation, successful `artifact_publish`, idempotent repeated publish, and refusal to publish malformed artifacts. Verify Bearer authentication remains mandatory.

- [x] **Step 2: Run controller tests and verify they fail**

Run: `mvn -q -Dtest=InternalAiToolsControllerTest test`

Expected: contract tests fail because validation is non-empty-only and publication is unsupported.

- [x] **Step 3: Integrate `ArtifactPipeline` behavior**

Replace non-empty validation with strict parse plus deterministic validation. Add `artifact_publish`, requiring `appId`, `codeGenType=MULTI_FILE`, `requestId`, `artifact`, `engine`, and `finishReason`. Return structured error maps and publication metadata. Keep the controller as an adapter; parsing and filesystem logic remain in artifact services. Add Chinese Javadoc to both adapter methods.

- [x] **Step 4: Run internal tool tests**

Run: `mvn -q -Dtest=InternalAiToolsControllerTest,ArtifactPublicationServiceTest test`

Expected: all tests pass.

- [x] **Step 5: Commit the tool contract**

```powershell
git add src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java
git commit -m "feat: 提供产物校验与发布内部工具"
```

### Task 8: Correct SSE terminal semantics and frontend error typing

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/AppController.java`
- Create: `src/test/java/com/yupi/yuaicodemother/controller/AppControllerSseTest.java`
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/src/api/app.ts`

- [x] **Step 1: Add failing SSE tests**

Verify successful content is followed by exactly one `done`; a generation exception produces exactly one `error` event containing numeric `code`, stable `errorCode`, `message`, and `requestId`; and an error stream never emits `done`.

- [x] **Step 2: Run SSE tests and verify they fail**

Run: `mvn -q -Dtest=AppControllerSseTest test`

Expected: failure because the controller currently appends `done` and does not map terminal errors to an SSE event.

- [x] **Step 3: Map terminal errors explicitly**

Keep normal `{"d": chunk}` events unchanged. Append `done` only to a normally completed content stream, then use `onErrorResume` to emit one named `error` event. Add a Chinese method comment explaining why HTTP status changes are no longer possible after SSE streaming starts.

- [x] **Step 4: Extend frontend error typing**

Add optional `errorCode?: string` and `requestId?: string` to `ChatGenCodeBusinessError`. Preserve existing `onBusinessError` behavior, which already avoids preview refresh and success notification.

- [x] **Step 5: Run backend SSE tests and frontend type check**

Run:

```powershell
mvn -q -Dtest=AppControllerSseTest test
Set-Location D:/VibeForge/yu-ai-code-mother-frontend
npm run type-check
```

Expected: tests and type check pass.

- [x] **Step 6: Commit SSE and frontend changes**

Commit the backend and frontend changes in their respective repositories if the frontend directory is a separate Git repository. Do not include unrelated frontend changes.

### Task 9: Add finish reasons and safe publication to LangGraph

**Files:**
- Modify: `ai-service/src/ai_service/models/base.py`
- Modify: `ai-service/src/ai_service/models/openai_compatible.py`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/conftest.py`
- Modify: `ai-service/tests/test_api.py`

- [x] **Step 1: Add failing workflow tests**

Add tests that MULTI_FILE skips `project_build`, `LENGTH` ends with `failed`, invalid validation goes to repair without model review, repair is capped at two attempts, exhausted repair ends with `failed`, and `completed` appears only after `artifact_publish` returns `published=true`.

- [x] **Step 2: Run targeted Python tests and verify failure**

Run: `Set-Location ai-service; uv run pytest tests/test_api.py -q`

Expected: new terminal and publication assertions fail.

- [x] **Step 3: Extend `ModelTurn` and model adapter**

Add `finish_reason: str | None` and `token_usage: dict[str, int]`. Normalize `response.response_metadata["finish_reason"]`. Change `repair` to return `ModelTurn`, not a bare string, so repaired candidates carry the same metadata. Add Chinese docstrings to metadata normalization and updated model methods.

- [x] **Step 4: Rebuild the workflow gates**

Reject length/content-filter results before validation. Treat `validation.valid=false` as a direct repair condition. Route Vue alone through `project_build`; route MULTI_FILE through `artifact_publish` after hard validation and quality success. When either gate still fails at the repair limit, raise a workflow failure so the terminal event is `failed`. Add Chinese docstrings to decision functions and publication node.

- [x] **Step 5: Run all Python verification**

Run:

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Expected: all commands exit 0.

- [x] **Step 6: Commit LangGraph integration**

```powershell
git add ai-service/src/ai_service ai-service/tests
git commit -m "fix: 阻止 LangGraph 发布不完整产物"
```

### Task 10: Complete documentation and full verification

**Files:**
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-startup.md`
- Modify: `doc/ai-multifile-truncation-incident-handoff.md`
- Modify: `docs/superpowers/plans/2026-09-20-ai-multifile-safe-generation.md`

- [x] **Step 1: Document the implemented contract**

Describe strict three-file format, `artifact_validate`/`artifact_publish`, finish-reason handling, `.current` releases, three-version retention, generation exclusion, and SSE terminal semantics. Mark the incident as resolved only after all verification below passes. Do not claim real-model or end-to-end validation unless actually run.

- [x] **Step 2: Verify Chinese method documentation**

Inspect every newly created Java/Python source file and every materially changed core method. Confirm each public/core method has a concise Chinese Javadoc or docstring and no unrelated historical comments were rewritten.

- [x] **Step 3: Run Java verification**

Run:

```powershell
mvn clean test
mvn clean -DskipTests compile
```

Expected: both commands exit 0 with no test failures or compilation errors.

Actual: task-targeted Java tests and clean compilation exit 0. The full suite runs 38 tests with 37 passing; the pre-existing `YuAiCodeMotherApplicationTests.contextLoads` still errors because the local test context has no bean named `openAiChatModel`. This unrelated environment baseline was not changed as part of the truncation fix.

- [x] **Step 4: Run Python verification**

Run:

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Expected: all commands exit 0.

- [x] **Step 5: Run frontend verification**

Run:

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-frontend
npm run type-check
npm run build-only
```

Expected: both commands exit 0.

- [x] **Step 6: Check diffs and repository state**

Run `git diff --check`, inspect `git status --short`, and review every changed file. Preserve the pre-existing deletion of `doc/ai-service-phase-one-handoff.md` and all untracked `projects/` files unless the user separately requests otherwise.

- [x] **Step 7: Commit documentation and plan completion**

```powershell
git add ai-service/README.md doc/ai-service-startup.md doc/ai-multifile-truncation-incident-handoff.md docs/superpowers/plans/2026-09-20-ai-multifile-safe-generation.md
git commit -m "docs: 更新多文件安全生成说明"
```

