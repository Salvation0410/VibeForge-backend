# Circular Preview Spinner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复预览更新提示中的加载图被文字挤压成椭圆的问题，同时保留现有 30px 双环外观。

**Architecture:** 仅调整 `AppChatView.vue` 中现有加载图的 CSS 盒模型。通过禁止 Flex 收缩并固定 1:1 比例保证正圆，不修改组件结构、状态逻辑和提示文案。

**Tech Stack:** Vue 3、CSS、TypeScript、Vite。

---

### Task 1: Lock the Spinner to a Circle

**Files:**
- Modify: `D:/VibeForge/yu-ai-code-mother-frontend/src/pages/AppChatView.vue`

- [ ] **Step 1: Update the shared spinner style**

Add the following declarations to `.preview-spinner` after `position: relative`:

```css
flex: 0 0 auto;
aspect-ratio: 1;
```

Keep the existing width, height, gradients, pseudo-elements and animation unchanged. Do not modify `package-lock.json`.

- [ ] **Step 2: Verify the frontend**

Run:

```powershell
npm run type-check
npm run build-only
git diff --check
```

Expected: all commands exit 0; only existing Vite bundle warnings may remain.

- [ ] **Step 3: Inspect and commit the scoped change**

```powershell
git diff -- src/pages/AppChatView.vue
git status --short
git add src/pages/AppChatView.vue
git commit -m "fix: 保持预览加载图为正圆"
```

Expected: the CSS diff contains exactly the two sizing declarations, and the user's existing `package-lock.json` modification remains unstaged.
