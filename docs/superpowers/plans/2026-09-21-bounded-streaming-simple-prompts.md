# Bounded Streaming and Simple Prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 前端继续实时显示生成内容，同时把响应式文本限制为最近 2000 个字符，并将“优化提示”改成普通用户易懂的简短文案。

**Architecture:** 继续由 `generationStreamProgress` 在 Vue 响应式系统外接收 SSE 分片，以 80ms 为最小刷新间隔向页面发布累计字符数、耗时和有界文本尾部。聊天页只渲染快照，完成后仍从服务端聊天记录取得完整最终消息；提示词保持为按生成类型选择的纯函数，并保留图片和完整产物约束。

**Tech Stack:** Vue 3、TypeScript、浏览器 EventSource、Node.js test runner、Vite。

---

### Task 1: Add a Bounded Streaming Text Window

**Files:**
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/tests/generationStreamProgress.test.ts`
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/src/utils/generationStreamProgress.ts`

- [ ] **Step 1: Extend the test snapshot type and add a failing 2000-character window assertion**

Update the first test so it records the whole snapshot, passes `maxVisibleCharacters: 2_000`, and verifies both the full count and the bounded tail:

```typescript
test('10,000 chunks share one scheduled flush and retain only the latest 2,000 characters', () => {
  const scheduler = createScheduler()
  const updates: Array<{
    receivedCharacters: number
    elapsedMs: number
    visibleContent: string
  }> = []
  let now = 1_000
  const progress = createGenerationStreamProgress({
    intervalMs: 80,
    maxVisibleCharacters: 2_000,
    schedule: scheduler.schedule,
    cancelSchedule: scheduler.cancelSchedule,
    now: () => now,
    onFlush: (snapshot) => updates.push(snapshot),
  })

  for (let index = 0; index < 10_000; index += 1) {
    progress.push(String(index % 10))
  }

  assert.equal(scheduler.tasks.length, 1)
  assert.deepEqual(updates, [])

  now = 1_080
  scheduler.runPending()
  assert.equal(updates.length, 1)
  assert.equal(updates[0]?.receivedCharacters, 10_000)
  assert.equal(updates[0]?.elapsedMs, 80)
  assert.equal(updates[0]?.visibleContent.length, 2_000)
  assert.equal(updates[0]?.visibleContent, '0123456789'.repeat(200))
})
```

Add a focused boundary test proving an oversized single chunk also stays bounded:

```typescript
test('visible content keeps the output tail when a single chunk exceeds the limit', () => {
  const scheduler = createScheduler()
  const updates: Array<{ receivedCharacters: number; visibleContent: string }> = []
  const progress = createGenerationStreamProgress({
    intervalMs: 80,
    maxVisibleCharacters: 5,
    schedule: scheduler.schedule,
    cancelSchedule: scheduler.cancelSchedule,
    onFlush: ({ receivedCharacters, visibleContent }) =>
      updates.push({ receivedCharacters, visibleContent }),
  })

  progress.push('abcdefgh')
  scheduler.runPending()

  assert.deepEqual(updates, [{ receivedCharacters: 8, visibleContent: 'defgh' }])
})
```

Add `maxVisibleCharacters: 2_000` to the existing `finish` and `dispose` test setup objects. Keep their existing assertions so terminal behavior remains covered.

- [ ] **Step 2: Run the focused test and verify the red state**

Run:

```powershell
node --test --experimental-strip-types tests/generationStreamProgress.test.ts
```

Expected: TypeScript reports that `maxVisibleCharacters` is not part of `GenerationStreamProgressOptions`, or the new `visibleContent` assertions fail because the snapshot does not provide it.

- [ ] **Step 3: Implement the bounded tail in ordinary JavaScript state**

Update the types and implementation in `generationStreamProgress.ts`:

```typescript
export interface GenerationProgressSnapshot {
  receivedCharacters: number
  elapsedMs: number
  visibleContent: string
}

export interface GenerationStreamProgressOptions<TScheduleHandle> {
  intervalMs: number
  maxVisibleCharacters: number
  schedule: (callback: () => void, delayMs: number) => TScheduleHandle
  cancelSchedule: (handle: TScheduleHandle) => void
  onFlush: (snapshot: GenerationProgressSnapshot) => void
  now?: () => number
}
```

Destructure `maxVisibleCharacters`, initialize `let visibleContent = ''`, and replace the current function comment with:

```typescript
/**
 * 在 Vue 响应式状态之外累计流式响应，只按固定间隔发布总字符数和有限长度的文本尾部。
 * 这样用户能实时看到内容，同时避免完整源码在每个分片到达时反复复制和渲染。
 */
```

Inside `push`, after increasing `receivedCharacters`, retain only the bounded tail without first joining the entire incoming chunk:

```typescript
const chunkTail = chunk.slice(-maxVisibleCharacters)
visibleContent = `${visibleContent}${chunkTail}`.slice(-maxVisibleCharacters)
dirty = true
```

Update the Chinese method comment for `push` to:

```typescript
/** 累计总字符数并保留最新文本尾部；同一刷新窗口内收到再多分片也只安排一次 UI 更新。 */
```

Include `visibleContent` in `flush()`:

```typescript
onFlush({
  receivedCharacters,
  elapsedMs: Math.max(0, now() - startedAt),
  visibleContent,
})
```

Do not store or expose the complete generated source. Keep the existing Chinese comments on `finish()` and `dispose()` because they already document final flush and cancellation semantics.

- [ ] **Step 4: Run the focused test and verify the green state**

Run:

```powershell
node --test --experimental-strip-types tests/generationStreamProgress.test.ts
```

Expected: all progress tests pass; the 10,000 chunks still schedule one flush, the visible content is exactly 2,000 characters, and terminal tests remain green.

- [ ] **Step 5: Commit only the bounded progress utility and tests**

```powershell
git add src/utils/generationStreamProgress.ts tests/generationStreamProgress.test.ts
git commit -m "feat: 显示有限长度的生成内容"
```

Expected: commit succeeds and does not stage the user's existing `package-lock.json` modification.

### Task 2: Replace Optimization Prompts with Short Plain Language

**Files:**
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/tests/optimizePrompt.test.ts`
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/src/utils/optimizePrompt.ts`

- [ ] **Step 1: Replace existing prompt tests with failing plain-language contracts**

Use these tests so the shortened wording, image preservation, complete output, and type-specific requirements remain explicit:

```typescript
import test from 'node:test'
import assert from 'node:assert/strict'
import { buildOptimizePrompt } from '../src/utils/optimizePrompt.ts'

test('HTML prompt is short and requests one complete runnable file', () => {
  const prompt = buildOptimizePrompt('HTML')
  assert.match(prompt, /完整可运行的 HTML 文件/)
  assert.match(prompt, /不要附带解释/)
  assert.match(prompt, /不要输出残缺代码/)
  assert.ok(prompt.length < 180)
})

test('MULTI_FILE prompt requests all three complete files', () => {
  const prompt = buildOptimizePrompt('multi_file')
  assert.match(prompt, /index\.html、style\.css 和 script\.js/)
  assert.match(prompt, /不要附带解释/)
  assert.match(prompt, /不要输出残缺代码/)
  assert.ok(prompt.length < 180)
})

test('VUE_PROJECT and unknown types limit changes to the existing project', () => {
  for (const type of ['VUE_PROJECT', 'unknown', undefined]) {
    const prompt = buildOptimizePrompt(type)
    assert.match(prompt, /只修改完成这次优化需要的文件/)
    assert.match(prompt, /不要重建项目/)
    assert.match(prompt, /正常运行/)
    assert.ok(prompt.length < 220)
  }
})

test('all optimization prompts preserve content, behavior, and images', () => {
  for (const type of ['HTML', 'MULTI_FILE', 'VUE_PROJECT', undefined]) {
    const prompt = buildOptimizePrompt(type)
    assert.match(prompt, /保留原有功能、文字、图片和操作方式/)
    assert.match(prompt, /不要删除或替换原有图片/)
    assert.match(prompt, /不要添加无关内容/)
    assert.match(prompt, /不要附带解释/)
    assert.match(prompt, /不要输出残缺/)
  }
})

test('prompts avoid wording that ordinary users should not need to understand', () => {
  for (const type of ['HTML', 'MULTI_FILE', 'VUE_PROJECT', undefined]) {
    const prompt = buildOptimizePrompt(type)
    assert.doesNotMatch(prompt, /信息层级|响应式表现|技术栈|输出协议|组件职责|代码块|围栏/)
  }
})
```

- [ ] **Step 2: Run the prompt test and verify the red state**

Run:

```powershell
node --test --experimental-strip-types tests/optimizePrompt.test.ts
```

Expected: tests fail because current prompts contain the rejected technical terms and exceed the new concise wording limits.

- [ ] **Step 3: Replace prompt constants and simplify the selector**

Replace `optimizePrompt.ts` with:

```typescript
const SHARED_REQUEST =
  '请让当前页面更美观、更清晰，并适合电脑和手机使用。保留原有功能、文字、图片和操作方式。不要删除或替换原有图片，也不要添加无关内容。'

const HTML_PROMPT = `${SHARED_REQUEST}请只返回一个完整可运行的 HTML 文件，不要附带解释；如果无法完整生成，请不要输出残缺代码。`

const MULTI_FILE_PROMPT = `${SHARED_REQUEST}请完整返回 index.html、style.css 和 script.js，不要附带解释；如果无法完整生成，请不要输出残缺代码。`

const VUE_PROMPT = `${SHARED_REQUEST}只修改完成这次优化需要的文件，不要重建项目或添加无关功能，并确保修改后可以正常运行。请完整完成所有修改，不要附带解释；如果无法完整生成，请不要输出残缺内容。`

/** 根据应用类型生成简短易懂的优化要求，同时保护原有图片、功能和内容不被删除。 */
export function buildOptimizePrompt(codeGenType?: string): string {
  switch (codeGenType?.toUpperCase()) {
    case 'HTML':
      return HTML_PROMPT
    case 'MULTI_FILE':
      return MULTI_FILE_PROMPT
    default:
      return VUE_PROMPT
  }
}
```

The Chinese comment is required: it explains the method's user-facing purpose and preservation guarantee without technical jargon.

- [ ] **Step 4: Run the prompt test and verify the green state**

Run:

```powershell
node --test --experimental-strip-types tests/optimizePrompt.test.ts
```

Expected: all prompt tests pass for HTML, MULTI_FILE, VUE_PROJECT, unknown, and missing types.

- [ ] **Step 5: Commit only prompt implementation and tests**

```powershell
git add src/utils/optimizePrompt.ts tests/optimizePrompt.test.ts
git commit -m "refactor: 简化页面优化提示"
```

Expected: commit succeeds and leaves `package-lock.json` unstaged.

### Task 3: Render the Bounded Snapshot in the Chat Page

**Files:**
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/src/pages/AppChatView.vue`

- [ ] **Step 1: Add the 2000-character UI boundary next to the existing refresh interval**

Locate `STREAM_PROGRESS_INTERVAL_MS` and define the companion limit:

```typescript
const STREAM_PROGRESS_INTERVAL_MS = 80
const STREAM_VISIBLE_CHARACTER_LIMIT = 2_000
```

Keep both values outside reactive state. Add this Chinese comment above the constants:

```typescript
// 流式内容最多每 80ms 刷新一次且只显示末尾 2000 个字符，避免长源码拖慢页面。
```

- [ ] **Step 2: Pass the limit and render the streamed text tail**

Update the existing `createGenerationStreamProgress` call:

```typescript
currentStreamProgress = createGenerationStreamProgress({
  intervalMs: STREAM_PROGRESS_INTERVAL_MS,
  maxVisibleCharacters: STREAM_VISIBLE_CHARACTER_LIMIT,
  schedule: (callback, delayMs) => window.setTimeout(callback, delayMs),
  cancelSchedule: (handle) => window.clearTimeout(handle),
  onFlush: ({ receivedCharacters, elapsedMs, visibleContent }) => {
    if (runId !== generationRunId) {
      return
    }
    const elapsedSeconds = (elapsedMs / 1000).toFixed(1)
    updateMessageContent(
      assistantMessageId,
      () =>
        `正在生成，已接收 ${receivedCharacters} 个字符，已用时 ${elapsedSeconds} 秒\n\n${visibleContent}`,
    )
  },
})
```

Do not concatenate chunks in `messages`, a Vue `ref`, or the component itself. Keep the existing `runId` check, `finish()` on `onDone`, `dispose()` on failures, `generationRunId` increment on reset/unmount, and final `loadHistoryPage(false)` call unchanged; together they ensure late callbacks are ignored and the completed message comes from the server.

- [ ] **Step 3: Run all focused frontend tests**

Run:

```powershell
node --test --experimental-strip-types tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts
```

Expected: all tests pass, including one scheduled refresh for 10,000 chunks, a maximum 2,000-character visible tail, terminal cancellation, concise prompts, and single preview refresh behavior.

- [ ] **Step 4: Run type checking and production build**

Run:

```powershell
npm run type-check
npm run build-only
```

Expected: both commands exit 0. Existing Vite bundle-size warnings are acceptable; TypeScript errors are not.

- [ ] **Step 5: Check the frontend diff and commit the chat integration**

```powershell
git diff --check
git status --short
git diff -- src/pages/AppChatView.vue
git add src/pages/AppChatView.vue
git commit -m "feat: 在生成进度中显示最新内容"
```

Expected: the diff contains only the intended chat-page integration; `package-lock.json` remains modified and unstaged.

### Task 4: Final Cross-Workspace Verification

**Files:**
- Read only: `D:/VibeForge/yu-ai-code-mother-frontend/package-lock.json`
- Read only: `D:/VibeForge/yu-ai-code-mother/src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java`
- Read only: `D:/VibeForge/yu-ai-code-mother/doc/ai-service-phase-one-handoff.md`
- Exclude: `D:/VibeForge/yu-ai-code-mother/projects/`

- [ ] **Step 1: Re-run the complete requested frontend verification**

```powershell
node --test --experimental-strip-types tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts
npm run type-check
npm run build-only
git diff --check
```

Expected: every command exits 0. Do not start the Python AI service; the user will run it from the IDE.

- [ ] **Step 2: Confirm repository boundaries and preserved user changes**

```powershell
git status --short
git -C D:/VibeForge/yu-ai-code-mother status --short
git -C D:/VibeForge/yu-ai-code-mother diff --check
```

Expected: frontend shows only the user's pre-existing `package-lock.json` modification after task commits. Backend still shows the user's deletion of `doc/ai-service-phase-one-handoff.md`, modification of `AppServiceImpl.java`, and untracked `projects/`; none are staged or changed by this plan.

- [ ] **Step 3: Record the behavior that still requires a running AI service**

Do not claim an end-to-end generation pass unless the user has started the Python AI service in the IDE. Report that automated tests, type checking, and production build verify the bounded update path, while a real second-round generation remains a manual acceptance check after the service is available.
