# HTML Safe Publication and Streaming Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent incomplete single-file HTML generations from replacing a working application, and keep the generation workspace responsive while large model responses stream.

**Architecture:** Spring adds a strict HTML parser, deterministic validator, injectable browser smoke-test boundary, and HTML support in the existing immutable release publisher. Legacy and LangGraph both publish through that Spring boundary before emitting success. The Vue client counts SSE content outside reactive state, flushes lightweight progress every 80 ms, keeps the previous preview mounted during generation, refreshes it once after `done`, and uses generation-type-specific optimization prompts.

**Tech Stack:** Java 21, Spring Boot 3.5.4, Reactor, LangChain4j, Selenium/WebDriver, JUnit 5, FastAPI, LangGraph, pytest, Vue 3, TypeScript, Node test runner

**Comment requirement:** Every newly added or materially changed core method must have concise Chinese Javadoc/docstrings explaining its responsibility and important failure semantics. Do not rewrite unrelated historical comments.

---

## File Responsibility Map

Backend parsing and publication:

- Create `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlArtifactParser.java`: accepts one closed HTML fence or pure complete HTML.
- Create `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlArtifactValidator.java`: deterministic document, CSS, and JavaScript completeness checks.
- Create `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlSmokeTestResult.java`: structured smoke-test result.
- Create `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlSmokeTester.java`: injectable smoke-test contract.
- Create `src/main/java/com/yupi/yuaicodemother/core/artifact/SeleniumHtmlSmokeTester.java`: production WebDriver implementation.
- Create `src/main/java/com/yupi/yuaicodemother/core/artifact/VersionedArtifactStore.java`: shared immutable release, tombstone, sequence, pointer, and retention storage.
- Modify `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPublicationService.java`: orchestrates MULTI_FILE and HTML parse/validate/smoke/publish.
- Modify `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPathResolver.java`: validates HTML releases according to their manifest files.

Generation integration:

- Modify `src/main/java/com/yupi/yuaicodemother/core/AiCodeGeneratorFacade.java`: Legacy HTML uses finish metadata and publishes before completion.
- Modify `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`: type-aware HTML validation and publication.
- Modify `ai-service/src/ai_service/orchestration/workflow.py`: HTML follows the publication node before completion.
- Modify `src/main/resources/prompt/codegen-html-system-prompt.txt`: exactly one complete HTML block, no surrounding prose.
- Create `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlOutputBudgetGuard.java`: rejects unsafe full rewrites of oversized active HTML.
- Modify `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`: applies the HTML rewrite budget before model invocation.

Frontend responsiveness:

- Create `src/utils/generationStreamProgress.ts`: non-reactive chunk counter with 80 ms progress flush.
- Create `src/utils/optimizePrompt.ts`: generation-type-specific optimization prompt templates.
- Modify `src/pages/AppChatView.vue`: lightweight progress, stop action, throttled scrolling, old-preview retention, and one success refresh.
- Modify `src/api/app.ts`: consume the standardized `business-error` SSE event and preserve request ID/error metadata.

Documentation and recovery:

- Modify `ai-service/README.md` and `doc/ai-service-startup.md`.
- Create `scripts/restore-html-release.ps1`: explicit dry-run-first recovery from a supplied complete candidate file.

### Task 1: Add strict single-HTML parsing

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlArtifactParser.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/core/paser/CodeParserExecutor.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/core/CodeParserTest.java`

- [x] **Step 1: Add failing parser regressions**

Add tests with these exact expectations:

```java
assertEquals("<!doctype html><html><head></head><body></body></html>",
        parser.parse("```html\n<!doctype html><html><head></head><body></body></html>\n```").getHtmlCode());
assertThrowsWithCode("HTML_FORMAT_INVALID", () -> parser.parse(
        "以下是优化后的代码：\n```html\n<html><body><script>${escapeText"));
assertThrowsWithCode("HTML_FORMAT_INVALID", () -> parser.parse(
        "```html\n<html><body></body></html>\n```\n额外说明"));
assertThrowsWithCode("HTML_FORMAT_INVALID", () -> parser.parse(
        "```html\n<html></html>\n```\n```html\n<html></html>\n```"));
```

Also cover pure HTML whose first non-whitespace token is `<!doctype html>` or `<html` and whose final non-whitespace token is `</html>`.

- [x] **Step 2: Run the parser test and verify red state**

Run:

```powershell
mvn -q -Dtest=CodeParserTest test
```

Expected: compilation fails because `HtmlArtifactParser` does not exist.

- [x] **Step 3: Implement the strict parser**

Expose:

```java
public HtmlCodeResult parse(String rawArtifact)
```

Use anchored patterns for the two accepted forms. Do not call the old permissive `HtmlCodeParser`. Throw `new ArtifactValidationException("HTML_FORMAT_INVALID", "index.html", "HTML 响应必须是唯一且完整的文档")` for blank input, surrounding prose, duplicate blocks, missing closing fences, or non-HTML fallback text. Add Chinese Javadoc explaining that no partial result is returned.

Update `CodeParserExecutor` so HTML delegates to this parser; retain the existing static executor API for callers.

- [x] **Step 4: Run parser tests**

Run:

```powershell
mvn -q -Dtest=CodeParserTest test
```

Expected: all parser tests pass, including the incident prefix and `${escapeText` truncation case.

- [x] **Step 5: Commit strict parsing**

```powershell
git add src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlArtifactParser.java src/main/java/com/yupi/yuaicodemother/core/paser/CodeParserExecutor.java
git add -f src/test/java/com/yupi/yuaicodemother/core/CodeParserTest.java
git commit -m "fix: 严格解析单文件 HTML 产物"
```

### Task 2: Add deterministic HTML/CSS/JavaScript validation

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlArtifactValidator.java`
- Create: `src/test/java/com/yupi/yuaicodemother/core/artifact/HtmlArtifactValidatorTest.java`

- [x] **Step 1: Add failing validator tests**

Cover these stable failures:

```text
HTML_DOCUMENT_INCOMPLETE       missing </html>, </body>, or </head>
HTML_MARKDOWN_RESIDUE          any ``` remains
HTML_STYLE_INCOMPLETE          unclosed style/comment/string or unbalanced braces
HTML_SCRIPT_INCOMPLETE         unclosed script/template/comment or unbalanced (), [], {}
HTML_SCRIPT_TRAILING_FRAGMENT  final meaningful token is an unfinished operator/expression
```

Use the incident tail ``list.innerHTML = displayList.map(msg => `${escapeText`` as a required rejection. Add passing cases for CSS braces inside strings/comments, JavaScript template literals with closed interpolations, regex literals, and empty optional script/style sets.

- [x] **Step 2: Run validator tests and verify red state**

Run:

```powershell
mvn -q -Dtest=HtmlArtifactValidatorTest test
```

Expected: compilation fails because the validator does not exist.

- [x] **Step 3: Implement one-pass validation**

Expose:

```java
public ArtifactValidationResult validate(HtmlCodeResult artifact)
public void validateOrThrow(HtmlCodeResult artifact)
```

Check original source boundaries before any tolerant DOM parsing. Reuse small package-private delimiter scanners only where semantics are identical to `MultiFileArtifactValidator`; do not silently normalize or append missing tags. `validateOrThrow` must use `HTML_VALIDATION_FAILED` and preserve the first file-specific detail while `validate` returns all errors.

Add Chinese Javadoc to both public methods and Chinese comments before stateful scanners explaining string/comment/template handling.

- [x] **Step 4: Run parser and validator tests**

```powershell
mvn -q "-Dtest=CodeParserTest,HtmlArtifactValidatorTest,MultiFileArtifactValidatorTest" test
```

Expected: all tests pass and existing MULTI_FILE validation remains unchanged.

- [x] **Step 5: Commit deterministic validation**

```powershell
git add src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlArtifactValidator.java
git add -f src/test/java/com/yupi/yuaicodemother/core/artifact/HtmlArtifactValidatorTest.java
git commit -m "feat: 增加单文件 HTML 确定性校验"
```

### Task 3: Extract a shared immutable release store and publish HTML versions

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/VersionedArtifactStore.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPublicationService.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPathResolver.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/core/artifact/ArtifactPublicationServiceTest.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/core/artifact/ArtifactPathResolverTest.java`

- [x] **Step 1: Add HTML release tests before refactoring**

Test `publishHtml` for first publication, same-request idempotency, same-request hash conflict, failed pointer replacement preserving the previous release, permanent request tombstone, monotonic replay rejection, current plus two previous entity releases, and legacy flat-directory fallback.

The intended public signature is:

```java
public ArtifactPublishResult publishHtml(
        long appId, String requestId, String rawArtifact, String engine, String finishReason)
```

Resolver assertions must require only `index.html` for an HTML release and all three files for a MULTI_FILE release.

- [x] **Step 2: Run publication tests and verify red state**

```powershell
mvn -q "-Dtest=ArtifactPublicationServiceTest,ArtifactPathResolverTest" test
```

Expected: HTML publication assertions fail because only MULTI_FILE is supported.

- [x] **Step 3: Extract `VersionedArtifactStore` without behavior changes**

Move release storage mechanics out of `ArtifactPublicationService` behind:

```java
ArtifactPublishResult publish(
        CodeGenTypeEnum type,
        long appId,
        String requestId,
        Map<String, String> files,
        String engine,
        String finishReason,
        StagingVerifier verifier)
```

`StagingVerifier.verify(Path staging)` runs after disk hash verification and before release move. Preserve sequence, tombstone, replay, atomic pointer, and retention behavior exactly. Manifest hashes determine required files; resolver validates every manifest hash entry and rejects path separators in manifest filenames.

Add Chinese Javadoc to publication, existing-release recovery, tombstone, pointer, and cleanup methods, including the rule that cleanup failure cannot reverse a committed version.

- [x] **Step 4: Implement HTML orchestration**

`ArtifactPublicationService.publishHtml` must:

1. validate request ID;
2. parse with `HtmlArtifactParser`;
3. call `HtmlArtifactValidator.validateOrThrow`;
4. call the shared store with `Map.of("index.html", html)`;
5. execute inside `GenerationLeaseService.commit` so cancellation and commit retain one winner.

Keep `publishMultiFile` public behavior and error codes unchanged.

- [x] **Step 5: Run artifact tests**

```powershell
mvn -q "-Dtest=CodeParserTest,HtmlArtifactValidatorTest,MultiFileArtifactValidatorTest,ArtifactPublicationServiceTest,ArtifactPathResolverTest" test
```

Expected: all tests pass for both HTML and MULTI_FILE.

- [x] **Step 6: Commit shared publication support**

```powershell
git add src/main/java/com/yupi/yuaicodemother/core/artifact
git add -f src/test/java/com/yupi/yuaicodemother/core/artifact
git commit -m "feat: 为 HTML 增加不可变版本发布"
```

### Task 4: Add an injectable Selenium smoke-test gate

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlSmokeTestResult.java`
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlSmokeTester.java`
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/SeleniumHtmlSmokeTester.java`
- Create: `src/main/java/com/yupi/yuaicodemother/config/HtmlArtifactProperties.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPublicationService.java`
- Modify: `src/main/resources/application.yml`
- Create: `src/test/java/com/yupi/yuaicodemother/core/artifact/SeleniumHtmlSmokeTesterTest.java`

- [ ] **Step 1: Add smoke-gate contract tests**

Use a fake `HtmlSmokeTester` in publication tests and assert:

```java
when(smokeTester.verify(any())).thenReturn(HtmlSmokeTestResult.failure(
        "HTML_SMOKE_TEST_FAILED", "JavaScript syntax error"));
assertThrowsWithCode("HTML_SMOKE_TEST_FAILED", () -> service.publishHtml(
        42L, "req-1", VALID_HTML, "legacy", "STOP"));
assertEquals(oldVersion, Files.readString(root.resolve(".current")));
```

Add Selenium integration tests tagged `@Tag("browser")` for a valid page, `const value = ;`, permanent `.skeleton` content, external image failure, and top-level navigation. Browser tests must use temporary files and never touch `tmp/code_output`.

- [ ] **Step 2: Run the fake-gate test and verify red state**

```powershell
mvn -q -Dtest=ArtifactPublicationServiceTest test
```

Expected: compilation fails because the smoke-test contract does not exist.

- [ ] **Step 3: Implement the contract and production tester**

Contract:

```java
public interface HtmlSmokeTester {
    HtmlSmokeTestResult verify(Path indexHtml);
}
```

The Selenium implementation loads `indexHtml.toUri()` using a dedicated WebDriver, waits at most 8 seconds for the document, observes 3 seconds, and enforces a 12-second total timeout. Capture browser console `SEVERE` entries, `window.onerror`, unhandled rejections, current URL, body text/visible element count, and visible loading/skeleton selectors.

Return `HTML_SMOKE_TEST_UNAVAILABLE` when Chrome/WebDriver cannot start. Do not reuse `WebScreenshotUtils`'s permissive timeout behavior; share only Chrome path lookup if extracted. Add Chinese comments explaining why inability to verify is a production-safe failure.

- [ ] **Step 4: Wire configuration and staging verification**

Add:

```yaml
ai:
  html-artifact:
    enabled: ${AI_HTML_SMOKE_TEST_ENABLED:true}
    required: ${AI_HTML_SMOKE_TEST_REQUIRED:true}
```

Production default is required. When `enabled=false` and `required=true`, fail with `HTML_SMOKE_TEST_UNAVAILABLE`. Only explicit development configuration may set both false and skip the gate with a warning.

Pass the staging `index.html` to the store's verifier before moving the release.

- [ ] **Step 5: Run smoke tests**

```powershell
mvn -q "-Dtest=ArtifactPublicationServiceTest,SeleniumHtmlSmokeTesterTest" test
```

Expected: unit tests pass. If local Chrome is unavailable, the tagged browser cases must report their direct environment reason; do not report them as passed.

- [ ] **Step 6: Commit the smoke gate**

```powershell
git add src/main/java/com/yupi/yuaicodemother/core/artifact src/main/java/com/yupi/yuaicodemother/config/HtmlArtifactProperties.java src/main/resources/application.yml
git add -f src/test/java/com/yupi/yuaicodemother/core/artifact
git commit -m "feat: 发布 HTML 前执行浏览器烟测"
```

### Task 5: Publish Legacy HTML before completing SSE

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/ai/AiCodeGeneratorService.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/core/AiCodeGeneratorFacade.java`
- Modify: `src/main/resources/prompt/codegen-html-system-prompt.txt`
- Modify: `src/test/java/com/yupi/yuaicodemother/core/AiCodeGeneratorFacadeTest.java`

- [ ] **Step 1: Add Legacy HTML completion tests**

Extend the fake `TokenStream` tests:

- `STOP` plus valid HTML calls `publishHtml` before Flux completion.
- `LENGTH` returns `MODEL_OUTPUT_TRUNCATED` and never calls the publisher.
- `STOP` plus the incident truncation returns `HTML_FORMAT_INVALID` or `HTML_VALIDATION_FAILED`.
- smoke/publish failure reaches the Flux error channel and never completes normally.
- a cancellation mark set before completion prevents publication.

- [ ] **Step 2: Run the Legacy tests and verify red state**

```powershell
mvn -q -Dtest=AiCodeGeneratorFacadeTest test
```

Expected: HTML cases fail because current completion still uses the old saver.

- [ ] **Step 3: Route HTML through `publishHtml`**

In the existing simple `TokenStream` adapter, collect chunks for HTML, call `ensureComplete(response.finishReason())`, then call:

```java
artifactPublicationService.publishHtml(
        appId, requestId, content.toString(), "legacy", finishReasonName);
```

Only call `sink.complete()` after publication returns. Never catch and log publication errors as success. Keep MULTI_FILE behavior intact. Add Chinese Javadoc explaining success and cancellation semantics.

- [ ] **Step 4: Tighten the system prompt**

Replace the permission for surrounding explanations with these exact requirements:

```text
最终回复只能包含一个完整且闭合的 html Markdown 代码块。
代码块外不得输出标题、解释、步骤或总结。
必须输出从 <!DOCTYPE html> 到 </html> 的完整文档；无法完整输出时不得提交部分代码。
```

- [ ] **Step 5: Run Legacy and artifact tests**

```powershell
mvn -q "-Dtest=AiCodeGeneratorFacadeTest,CodeParserTest,HtmlArtifactValidatorTest,ArtifactPublicationServiceTest" test
```

Expected: all tests pass.

- [ ] **Step 6: Commit Legacy integration**

```powershell
git add src/main/java/com/yupi/yuaicodemother/ai/AiCodeGeneratorService.java src/main/java/com/yupi/yuaicodemother/core/AiCodeGeneratorFacade.java src/main/resources/prompt/codegen-html-system-prompt.txt
git add -f src/test/java/com/yupi/yuaicodemother/core/AiCodeGeneratorFacadeTest.java
git commit -m "fix: 仅在 HTML 安全发布后完成生成"
```

### Task 6: Route LangGraph HTML through Spring validation and publication

**Files:**
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Modify: `ai-service/tests/test_api.py`

- [ ] **Step 1: Add Spring tool contract tests**

Add `artifact_validate` HTML success and incident rejection, plus `artifact_publish` HTML success, idempotency, and smoke-test failure. Assert `codeGenType=HTML` selects the HTML pipeline and MULTI_FILE remains unchanged.

- [ ] **Step 2: Add Python workflow tests**

Assert HTML calls `artifact_publish`, `completed` appears only after `published=true`, publish rejection produces `failed`, and a post-publication graph checkpoint failure still emits exactly one `completed`.

- [ ] **Step 3: Run both test groups and verify red state**

```powershell
mvn -q -Dtest=InternalAiToolsControllerTest test
Set-Location ai-service
uv run pytest tests/test_api.py -q
```

Expected: HTML publication assertions fail in both services.

- [ ] **Step 4: Extend the Spring adapter**

Dispatch validation and publication by parsed `CodeGenTypeEnum`:

```java
case HTML -> artifactPublicationService.publishHtml(appId, requestId, artifact, engine, finishReason);
case MULTI_FILE -> artifactPublicationService.publishMultiFile(appId, requestId, artifact, engine, finishReason);
default -> throw new BusinessException(ErrorCode.PARAMS_ERROR, "artifact_publish does not support " + type);
```

Keep the controller free of filesystem logic. Return structured validation errors and existing publication metadata.

- [ ] **Step 5: Change LangGraph routing**

Update `_after_review` so both `HTML` and `MULTI_FILE` route to `artifact_publish`. Treat both types as committed publication terminal paths in checkpoint fallback logic:

```python
return "publish" if state["code_gen_type"] in {"HTML", "MULTI_FILE"} else "finalize"
```

Keep Vue build/finalize behavior unchanged. Add Chinese docstrings for the changed routing and terminal behavior.

- [ ] **Step 6: Run cross-service tests**

```powershell
mvn -q "-Dtest=InternalAiToolsControllerTest,ArtifactPublicationServiceTest,LangGraphAiGenerationGatewayTest" test
Set-Location ai-service
uv run pytest
uv run python -m compileall -q src
uv lock --check
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit LangGraph integration**

```powershell
git add src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java ai-service/src/ai_service/orchestration/workflow.py ai-service/tests/test_api.py
git add -f src/test/java/com/yupi/yuaicodemother/controller/InternalAiToolsControllerTest.java
git commit -m "feat: 统一发布 LangGraph HTML 产物"
```

### Task 7: Reject unsafe oversized HTML rewrites

**Files:**
- Create: `src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlOutputBudgetGuard.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/config/HtmlArtifactProperties.java`
- Modify: `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`
- Modify: `src/main/resources/application.yml`
- Create: `src/test/java/com/yupi/yuaicodemother/core/artifact/HtmlOutputBudgetGuardTest.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/service/impl/AppServiceGenerationCancellationTest.java`

- [ ] **Step 1: Add budget guard tests**

Test no active HTML, HTML below threshold, HTML at threshold, HTML above threshold, and non-HTML types. Above-threshold HTML must throw `HTML_OUTPUT_BUDGET_EXCEEDED` before chat history insertion or model invocation.

- [ ] **Step 2: Run tests and verify red state**

```powershell
mvn -q "-Dtest=HtmlOutputBudgetGuardTest,AppServiceGenerationCancellationTest" test
```

Expected: compilation fails because the guard does not exist.

- [ ] **Step 3: Implement and integrate the guard**

Add configuration:

```yaml
ai:
  html-artifact:
    max-rewrite-source-chars: ${AI_HTML_MAX_REWRITE_SOURCE_CHARS:24000}
```

Expose:

```java
public void checkRewriteAllowed(CodeGenTypeEnum type, long appId)
```

Resolve the active HTML root, read `index.html` size as UTF-8 characters, and throw `ArtifactValidationException("HTML_OUTPUT_BUDGET_EXCEEDED", "index.html", "当前单文件页面过大，请迁移为多文件应用后继续优化")` when above the configured limit. `AppServiceImpl.wrapGenerationError` adds the current request ID. Run the guard after permission checks and lease acquisition but before history insertion/model invocation. Missing first-version files are allowed.

- [ ] **Step 4: Run service tests**

```powershell
mvn -q "-Dtest=HtmlOutputBudgetGuardTest,AppServiceGenerationCancellationTest" test
```

Expected: all tests pass and rejected requests do not pollute history.

- [ ] **Step 5: Commit the budget guard**

```powershell
git add src/main/java/com/yupi/yuaicodemother/core/artifact/HtmlOutputBudgetGuard.java src/main/java/com/yupi/yuaicodemother/config/HtmlArtifactProperties.java src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java src/main/resources/application.yml
git add -f src/test/java/com/yupi/yuaicodemother/core/artifact/HtmlOutputBudgetGuardTest.java src/test/java/com/yupi/yuaicodemother/service/impl/AppServiceGenerationCancellationTest.java
git commit -m "feat: 阻止超预算 HTML 全量重写"
```

### Task 8: Add generation-type-specific optimization prompts

**Files:**
- Create: `src/utils/optimizePrompt.ts`
- Create: `tests/optimizePrompt.test.ts`
- Modify: `src/pages/AppChatView.vue`

- [x] **Step 1: Add prompt selection tests**

Use the exact three templates approved in the design spec. Tests must assert:

```typescript
assert.match(buildOptimizePrompt('HTML'), /一个闭合的 html 代码块/)
assert.match(buildOptimizePrompt('MULTI_FILE'), /index\.html、style\.css 和 script\.js/)
assert.match(buildOptimizePrompt('VUE_PROJECT'), /不要重建工程/)
assert.doesNotMatch(buildOptimizePrompt('HTML'), /补全.*加载.*空状态.*动画/)
```

Unknown types must use the conservative Vue-style “minimal required changes” template without inventing an output protocol.

- [x] **Step 2: Run the Node test and verify red state**

```powershell
node --test --experimental-strip-types tests/optimizePrompt.test.ts
```

Expected: module-not-found failure for `src/utils/optimizePrompt.ts`.

- [x] **Step 3: Implement prompt selection**

Expose:

```typescript
export function buildOptimizePrompt(codeGenType?: string): string
```

Normalize type with `toUpperCase()` and return the exact templates from design section 13. In `useOptimizePrompt`, call `buildOptimizePrompt(appDetail.value?.codeGenType)`.

Use these complete constants:

```typescript
const HTML_PROMPT = '请基于当前应用的完整代码优化页面。必须保留现有业务功能、数据内容和交互逻辑，只调整信息层级、排版、间距、配色、响应式表现和必要的操作反馈；不要新增与需求无关的模块、加载动画或模拟数据。请返回修改后的完整单文件 HTML，CSS 和 JavaScript 必须内联且结构完整。最终回复只能包含一个闭合的 html 代码块，代码块外不得输出标题、解释或总结；无法完整输出时不要提交部分代码。'

const MULTI_FILE_PROMPT = '请基于当前应用的完整代码优化页面。必须保留现有业务功能、数据内容和交互逻辑，只调整信息层级、排版、间距、配色、响应式表现和必要的操作反馈；不要新增与需求无关的模块、加载动画或模拟数据。请返回完整的 index.html、style.css 和 script.js，三个文件必须相互匹配且可直接运行。严格按当前三文件协议输出，围栏外不得输出标题、解释或总结；无法完整输出时不要提交部分代码。'

const VUE_PROMPT = '请在当前 Vue 工程内优化页面。必须保留现有路由、组件职责、业务功能、数据内容和交互逻辑，只修改完成本次视觉与体验优化所必需的文件；不要重建工程、替换技术栈、引入无关依赖、模块、动画或模拟数据。优先复用现有组件和样式约定，确保修改后项目能够正常构建，并完整完成所有必要文件修改。'
```

- [x] **Step 4: Run prompt and type checks**

```powershell
node --test --experimental-strip-types tests/optimizePrompt.test.ts
npm run type-check
```

Expected: both exit 0.

- [x] **Step 5: Commit prompt optimization in the frontend repository**

```powershell
git add src/utils/optimizePrompt.ts src/pages/AppChatView.vue tests/optimizePrompt.test.ts
git commit -m "feat: 按生成类型优化改版提示"
```

Do not stage the user's pre-existing `package-lock.json` change.

### Task 9: Batch SSE progress and stop rendering full source per chunk

**Files:**
- Create: `src/utils/generationStreamProgress.ts`
- Create: `tests/generationStreamProgress.test.ts`
- Modify: `src/pages/AppChatView.vue`
- Modify: `src/api/app.ts`
- Modify: `src/main/java/com/yupi/yuaicodemother/controller/AppController.java`
- Modify: `src/test/java/com/yupi/yuaicodemother/controller/AppControllerSseTest.java`

- [ ] **Step 1: Add deterministic progress-buffer tests**

Inject scheduler functions so Node tests need no browser timers. Cover 10,000 chunks, one scheduled flush per 80 ms window, accumulated character count, final flush, cancel cleanup, and no callback after disposal.

Expected API:

```typescript
createGenerationStreamProgress({
  intervalMs: 80,
  schedule,
  cancelSchedule,
  onFlush: (snapshot) => updates.push(snapshot),
})
```

The accumulator stores only character count and timing metadata, never concatenated source text.

- [ ] **Step 2: Run the buffer test and verify red state**

```powershell
node --test --experimental-strip-types tests/generationStreamProgress.test.ts
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement the non-reactive accumulator**

Expose `push(chunk)`, `finish()`, and `dispose()`. Chinese comments must state that source chunks intentionally stay outside Vue reactive state to avoid repeated full-text DOM updates.

- [ ] **Step 4: Standardize the business failure event**

Change `AppController` to emit `event("business-error")` for structured generation failures. Keep native EventSource `error` exclusively for transport/protocol failures. Preserve `code`, `errorCode`, `message`, and `requestId` in the JSON body.

Update `AppControllerSseTest` to assert one `business-error`, no `done`, and no named `error` event. Keep `src/api/app.ts` listening to `business-error` and its existing native `onerror` handler for network failures.

- [ ] **Step 5: Integrate lightweight progress UI**

In `AppChatView.vue`:

- create the assistant placeholder as `正在生成，已接收 0 个字符`;
- `onMessage` only calls `progress.push(chunk)`;
- each 80 ms flush updates the placeholder with phase, character count, and elapsed seconds;
- `onDone` disposes progress, reloads history, and replaces the placeholder from authoritative history;
- error/cancel disposes progress and shows the stable error message;
- component unmount closes EventSource and disposes timers.

Add a “停止生成” button while streaming. Its click calls `currentEventSource?.close()`, clears local progress, leaves the previous preview intact, and relies on the existing server-side reactive cancellation path to prevent publication.

- [ ] **Step 6: Replace per-chunk smooth scrolling**

Throttle auto-follow to at most once per 200 ms and use `behavior: 'auto'` during streaming. Track whether the viewport is within 48 px of the bottom; pause following after the user scrolls upward and resume only when they return to the bottom.

- [ ] **Step 7: Run backend SSE and frontend verification**

```powershell
mvn -q -Dtest=AppControllerSseTest test
Set-Location C:/Users/ASUS/.config/superpowers/worktrees/yu-ai-code-mother-frontend/ai-multifile-safe-generation
node --test --experimental-strip-types tests/generationStreamProgress.test.ts tests/optimizePrompt.test.ts
npm run type-check
npm run build-only
```

Expected: tests, type check, and build exit 0; existing chunk-size warnings may remain.

- [ ] **Step 8: Commit responsive streaming changes in each repository**

```powershell
# Backend repository
git add src/main/java/com/yupi/yuaicodemother/controller/AppController.java
git add -f src/test/java/com/yupi/yuaicodemother/controller/AppControllerSseTest.java
git commit -m "fix: 统一生成业务失败 SSE 事件"

# Frontend repository
git add src/api/app.ts src/utils/generationStreamProgress.ts src/pages/AppChatView.vue tests/generationStreamProgress.test.ts
git commit -m "perf: 批量更新生成进度避免页面卡死"
```

Do not stage `package-lock.json`.

### Task 10: Keep the old preview and refresh exactly once after publication

**Files:**
- Modify: `src/pages/AppChatView.vue`
- Create: `src/utils/previewRefreshCoordinator.ts`
- Create: `tests/previewRefreshCoordinator.test.ts`

- [ ] **Step 1: Add refresh coordinator tests**

Assert that generation start performs zero reloads, success performs one reload, repeated success callbacks for the same request perform one reload, error/cancel performs zero reloads, and dispose cancels pending work.

- [ ] **Step 2: Run the coordinator test and verify red state**

```powershell
node --test --experimental-strip-types tests/previewRefreshCoordinator.test.ts
```

Expected: module-not-found failure.

- [ ] **Step 3: Implement one-shot refresh coordination**

Expose:

```typescript
createPreviewRefreshCoordinator(reload: () => Promise<void> | void)
```

with `begin(requestKey)`, `complete(requestKey)`, `fail(requestKey)`, and `dispose()`. Ignore duplicate or stale terminal calls.

- [ ] **Step 4: Remove speculative preview reloads**

In `sendMessage`, remove the generation-start `finalizePreview()` call. Keep the existing iframe/srcdoc mounted under the generating overlay. In `onDone`, reload app detail and history, then call the coordinator once. Delete `schedulePreviewReloads`, `pendingPreviewReloadTimers`, and the meta-refresh fallback; fetch failure must retain the old `previewSrcDoc` and show a retryable message.

Add Chinese comments to the changed methods explaining that preview refresh follows committed `done`, not timing guesses.

- [ ] **Step 5: Run frontend verification**

```powershell
node --test --experimental-strip-types tests/previewRefreshCoordinator.test.ts tests/generationStreamProgress.test.ts tests/optimizePrompt.test.ts
npm run type-check
npm run build-only
```

Expected: all exit 0.

- [ ] **Step 6: Commit one-shot preview refresh**

```powershell
git add src/pages/AppChatView.vue src/utils/previewRefreshCoordinator.ts tests/previewRefreshCoordinator.test.ts
git commit -m "fix: 仅在发布成功后刷新一次预览"
```

### Task 11: Add explicit dry-run HTML recovery

**Files:**
- Create: `scripts/restore-html-release.ps1`
- Create: `src/main/java/com/yupi/yuaicodemother/controller/admin/ArtifactRecoveryController.java`
- Create: `src/main/java/com/yupi/yuaicodemother/model/dto/app/HtmlArtifactRecoveryRequest.java`
- Create: `src/test/java/com/yupi/yuaicodemother/controller/admin/ArtifactRecoveryControllerTest.java`
- Modify: `doc/ai-service-startup.md`

- [ ] **Step 1: Add recovery endpoint tests**

Require admin authentication, `appId`, a new recovery `requestId`, candidate HTML, source description, and `dryRun=true` by default. Dry run executes parser, validator, and smoke test but does not create a release or change `.current`. Commit mode calls `publishHtml` with engine `manual-recovery`.

- [ ] **Step 2: Run recovery tests and verify red state**

```powershell
mvn -q -Dtest=ArtifactRecoveryControllerTest test
```

Expected: compilation fails because recovery types do not exist.

- [ ] **Step 3: Implement the admin recovery adapter**

Expose `POST /api/apps/admin/artifacts/html/recover` using the existing `@AuthCheck(mustRole = UserConstant.ADMIN_ROLE)` pattern. The controller must not search chat history automatically; the operator supplies an explicitly reviewed candidate and source description. Return validation/smoke details in dry run and publication metadata in commit mode.

- [ ] **Step 4: Implement the PowerShell wrapper**

Script parameters:

```powershell
param(
  [Parameter(Mandatory)] [Int64] $AppId,
  [Parameter(Mandatory)] [string] $CandidateFile,
  [Parameter(Mandatory)] [string] $RequestId,
  [Parameter(Mandatory)] [Microsoft.PowerShell.Commands.WebRequestSession] $WebSession,
  [string] $BaseUrl = 'http://localhost:8123/api',
  [switch] $Commit
)
```

The script reads UTF-8, sends dry run unless `-Commit` is present, passes `-WebSession $WebSession` to `Invoke-RestMethod`, and never reads or rewrites `projects/`. Do not embed credentials or session cookies.

- [ ] **Step 5: Run recovery tests**

```powershell
mvn -q "-Dtest=ArtifactRecoveryControllerTest,ArtifactPublicationServiceTest,HtmlArtifactValidatorTest" test
```

Expected: all tests pass.

- [ ] **Step 6: Commit recovery tooling**

```powershell
git add scripts/restore-html-release.ps1 src/main/java/com/yupi/yuaicodemother/controller/admin/ArtifactRecoveryController.java src/main/java/com/yupi/yuaicodemother/model/dto/app/HtmlArtifactRecoveryRequest.java doc/ai-service-startup.md
git add -f src/test/java/com/yupi/yuaicodemother/controller/admin/ArtifactRecoveryControllerTest.java
git commit -m "feat: 提供 HTML 版本显式恢复工具"
```

Do not run commit-mode recovery for application `459157197728309248` until a complete candidate has been identified and the user separately authorizes mutation of that application.

### Task 12: Final cross-service verification and deployment handoff

**Files:**
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-startup.md`
- Modify: `docs/superpowers/plans/2026-09-20-html-safe-publication-streaming-performance.md`

- [ ] **Step 1: Document the final contract**

Document HTML accepted formats, error codes, smoke-test configuration, rewrite-size guard, immutable directory layout, dynamic optimization prompts, frontend progress behavior, one-shot preview refresh, and explicit recovery steps.

- [ ] **Step 2: Verify Chinese method comments**

Review every created or materially changed core method. Confirm Chinese comments state responsibility and failure behavior for parser, validator, smoke tester, release store, publisher, workflow terminal handling, frontend progress buffer, preview coordinator, and recovery adapter.

- [ ] **Step 3: Run backend targeted verification**

```powershell
mvn -q "-Dtest=CodeParserTest,HtmlArtifactValidatorTest,MultiFileArtifactValidatorTest,ArtifactPublicationServiceTest,ArtifactPathResolverTest,SeleniumHtmlSmokeTesterTest,AiCodeGeneratorFacadeTest,InternalAiToolsControllerTest,LangGraphAiGenerationGatewayTest,AppServiceGenerationCancellationTest,AppControllerSseTest,ArtifactRecoveryControllerTest" test
mvn -q clean -DskipTests compile
```

Expected: both commands exit 0. Record browser-tagged cases separately if Chrome is unavailable.

- [ ] **Step 4: Run the full backend suite and record baseline status**

```powershell
mvn -q test
```

Expected task-related tests: pass. If `YuAiCodeMotherApplicationTests.contextLoads` still fails only because `openAiChatModel` is absent, record the exact count and reason; do not change unrelated model configuration or claim the full suite passed.

- [ ] **Step 5: Run Python verification**

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Expected: all commands exit 0.

- [ ] **Step 6: Run frontend verification**

```powershell
Set-Location C:/Users/ASUS/.config/superpowers/worktrees/yu-ai-code-mother-frontend/ai-multifile-safe-generation
node --test --experimental-strip-types tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts
npm run type-check
npm run build-only
```

Expected: all commands exit 0. Do not stage the pre-existing `package-lock.json` change.

- [ ] **Step 7: Perform local browser acceptance without generating a new application**

Start the backend from the implementation worktree, not `D:/VibeForge/yu-ai-code-mother/target/classes`. Confirm process command lines and health endpoints, then use a test fixture application to verify responsive streaming, cancellation, retained preview, one refresh after success, and failed truncated publication preserving `.current`.

Do not use application `459157197728309248` for mutation in this step.

- [ ] **Step 8: Check repository state**

```powershell
git diff --check
git status --short
git log --oneline --decorate -15
```

Backend worktree must be clean. Frontend worktree may contain only the user's pre-existing `package-lock.json` modification. Original workspaces and `projects/` must remain untouched.

- [ ] **Step 9: Commit documentation and plan completion**

```powershell
git add ai-service/README.md doc/ai-service-startup.md docs/superpowers/plans/2026-09-20-html-safe-publication-streaming-performance.md
git commit -m "docs: 更新 HTML 安全发布与流式性能说明"
```

Before deployment, explicitly merge/deploy the preceding MULTI_FILE safety branch and verify the running Spring classpath points to the merged build. A correct implementation that is not loaded by the running service does not resolve the incident.
