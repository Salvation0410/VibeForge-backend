# Active Workspace Rollout and Image Preservation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将已验证的 HTML 安全发布和前端流式性能修复接入实际运行的 `dev` 工作区，并确保二次优化保留全部现有图片且不显示冗余提示。

**Architecture:** 后端合入严格解析、确定性校验、浏览器烟测和不可变发布，前端合入非响应式进度缓冲与单次预览刷新。图片保护作为优化提示的独立契约，并通过纯函数测试验证；现有用户工作区修改先隔离保存，合并后再恢复和逐项核对。

**Tech Stack:** Java 21、Spring Boot、JUnit 5、Selenium、Vue 3、TypeScript、Vite、Node test runner、Git worktree。

---

### Task 1: Preserve Current Workspace State

**Files:**
- Preserve: `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`
- Preserve: `doc/ai-service-phase-one-handoff.md`
- Preserve: `D:/VibeForge/yu-ai-code-mother-frontend/src/pages/AppChatView.vue`
- Preserve: `D:/VibeForge/yu-ai-code-mother-frontend/package-lock.json`
- Exclude: `projects/`

- [ ] **Step 1: Record exact status and diffs**

```powershell
git status --short
git diff -- src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java doc/ai-service-phase-one-handoff.md
git -C D:/VibeForge/yu-ai-code-mother-frontend status --short
git -C D:/VibeForge/yu-ai-code-mother-frontend diff -- src/pages/AppChatView.vue package-lock.json
```

Expected: backend shows the existing service/document changes and untracked `projects/`; frontend shows `AppChatView.vue` and `package-lock.json` changes.

- [ ] **Step 2: Stash only tracked user changes**

```powershell
git stash push -m "pre-rollout-user-backend" -- src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java doc/ai-service-phase-one-handoff.md
git -C D:/VibeForge/yu-ai-code-mother-frontend stash push -m "pre-rollout-user-frontend" -- src/pages/AppChatView.vue package-lock.json
```

Expected: `projects/` remains untracked; the named stashes contain only the listed tracked files.

### Task 2: Integrate the Validated Backend Branch

**Files:**
- Merge source: `codex/ai-multifile-safe-generation`
- Restore: `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`
- Restore: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: Merge the validated branch without rewriting history**

```powershell
git merge --no-ff codex/ai-multifile-safe-generation -m "merge: 接入 HTML 安全发布与流式性能治理"
```

Expected: merge succeeds without touching `projects/`.

- [ ] **Step 2: Restore tracked user changes and resolve overlaps behaviorally**

```powershell
git stash pop stash^{/pre-rollout-user-backend}
```

If `AppServiceImpl.java` conflicts, retain both the user's pre-rollout behavior and the merged `HtmlOutputBudgetGuard` call placed after lease acquisition and before history/model invocation. Retain the user's deletion state for `doc/ai-service-phase-one-handoff.md`.

- [ ] **Step 3: Verify the backend integration**

```powershell
mvn -q "-Dtest=CodeParserTest,HtmlArtifactValidatorTest,ArtifactPublicationServiceTest,SeleniumHtmlSmokeTesterTest,AiCodeGeneratorFacadeTest,InternalAiToolsControllerTest,AppServiceGenerationCancellationTest,AppControllerSseTest" test
mvn -q clean -DskipTests compile
```

Expected: both commands exit 0; Selenium may log the known Chrome 153 CDP compatibility warning.

### Task 3: Integrate the Validated Frontend Branch

**Files:**
- Merge source: `codex/ai-multifile-safe-generation`
- Restore: `D:/VibeForge/yu-ai-code-mother-frontend/package-lock.json`
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/src/pages/AppChatView.vue`

- [ ] **Step 1: Merge the validated frontend branch**

```powershell
git -C D:/VibeForge/yu-ai-code-mother-frontend merge --no-ff codex/ai-multifile-safe-generation -m "merge: 接入生成流式性能与单次预览刷新"
git -C D:/VibeForge/yu-ai-code-mother-frontend stash pop stash^{/pre-rollout-user-frontend}
```

Expected: `package-lock.json` remains a user modification. If `AppChatView.vue` conflicts at `useOptimizePrompt`, keep `buildOptimizePrompt(appDetail.value?.codeGenType)` instead of restoring a single hard-coded prompt.

- [ ] **Step 2: Remove the redundant streaming tip**

Delete the template block and unused style for:

```vue
<div v-if="streaming" class="streaming-tip">AI 正在持续生成代码和说明，请稍候...</div>
```

Keep the compact progress placeholder and “停止生成” action.

- [ ] **Step 3: Verify the frontend integration**

```powershell
npx --yes tsx --test tests/*.test.ts
npm run type-check
npm run build-only
```

Expected: tests, type checking, and build exit 0; existing Vite chunk warnings may remain.

### Task 4: Enforce Image Preservation in Optimization Prompts

**Files:**
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/src/utils/optimizePrompt.ts`
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/tests/optimizePrompt.test.ts`

- [ ] **Step 1: Add failing image-contract tests**

Add assertions for HTML, MULTI_FILE, VUE_PROJECT, and unknown types:

```typescript
for (const type of ['HTML', 'MULTI_FILE', 'VUE_PROJECT', undefined]) {
  const prompt = buildOptimizePrompt(type)
  assert.match(prompt, /保留.*图片/)
  assert.match(prompt, /不得删除/)
  assert.match(prompt, /不得.*随机图|随机图片/)
}
assert.match(buildOptimizePrompt('HTML'), /禁止输出任何解释文字/)
```

- [ ] **Step 2: Run the prompt test and verify red state**

```powershell
node --test --experimental-strip-types tests/optimizePrompt.test.ts
```

Expected: image-preservation assertions fail.

- [ ] **Step 3: Add the shared media-preservation suffix**

Append this contract to every returned template:

```text
必须保留现有全部图片、图片地址、CSS 背景图和业务媒体数据；不得删除图片，不得替换为随机图、占位图或无关外链。允许调整图片尺寸、裁剪方式、响应式布局、懒加载和加载失败状态；原图片无法访问时仍保留原引用，不得伪造替代内容。
```

Add a Chinese comment to `buildOptimizePrompt` explaining that this shared suffix prevents optimization from silently deleting business media.

- [ ] **Step 4: Run tests and commit frontend changes**

```powershell
node --test --experimental-strip-types tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts
npm run type-check
npm run build-only
git add src/utils/optimizePrompt.ts src/pages/AppChatView.vue tests/optimizePrompt.test.ts
git commit -m "fix: 优化时保留图片并精简生成提示"
```

Expected: all checks exit 0; do not stage `package-lock.json`.

### Task 5: Verify Active Runtime and Regression Evidence

**Files:**
- Read only: `tmp/code_output/html_459466135980023808/index.html`

- [ ] **Step 1: Prove the old artifact failure mode**

```powershell
$html = Get-Content tmp/code_output/html_459466135980023808/index.html -Raw
@{
  HasNaturalLanguagePrefix = -not $html.TrimStart().StartsWith('<!DOCTYPE html>')
  ScriptClosed = $html.Contains('</script>')
  HtmlClosed = $html.Contains('</html>')
  HasImageData = $html.Contains('picsum.photos')
}
```

Expected: natural-language prefix true, script/html closed false, image data true. This records why the old page had a prompt and no rendered images without mutating it.

- [ ] **Step 2: Restart services from the integrated workspaces**

Stop only the identified project Spring/Python/Vite processes after verifying their command lines. Start Spring from `D:/VibeForge/yu-ai-code-mother`, Python from its `ai-service` directory, and Vite from `D:/VibeForge/yu-ai-code-mother-frontend`.

- [ ] **Step 3: Verify health and loaded behavior**

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
Invoke-WebRequest http://localhost:8123/api/doc.html -UseBasicParsing
Invoke-WebRequest http://localhost:5173 -UseBasicParsing
```

Expected: both AI health endpoints are healthy and Spring/Vite return HTTP success.

- [ ] **Step 4: Browser acceptance without mutating the accident application**

Open the existing chat page, confirm the redundant streaming tip is absent and the old preview remains visible. Do not start a new generation for application `459466135980023808`. Record that a fresh optimization must be tested on a separate fixture application before deployment acceptance.

- [ ] **Step 5: Final repository checks**

```powershell
git diff --check
git status --short
git -C D:/VibeForge/yu-ai-code-mother-frontend diff --check
git -C D:/VibeForge/yu-ai-code-mother-frontend status --short
```

Expected: backend retains only the user's pre-existing deletion/service modification and untracked `projects/` if not incorporated by conflict resolution; frontend retains only the user's pre-existing `package-lock.json` modification outside committed task changes.
