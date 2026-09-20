# AI Service Windows Local Startup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the AI service README with a reliable Windows PowerShell initialization and startup path that does not depend on uv's default managed-Python links.

**Architecture:** Keep the change documentation-only. Make the project-local workflow discover a real CPython 3.12.14 executable under `%LOCALAPPDATA%`, use it to create `.venv` and install locked dependencies, then start Uvicorn through that real executable with explicit import paths. Retain the existing uv commands as a shorter alternative for healthy uv installations.

**Tech Stack:** Markdown, Windows PowerShell, uv, CPython 3.12.14, Uvicorn

---

### Task 1: Replace the local startup documentation

**Files:**
- Modify: `ai-service/README.md:125`

- [x] **Step 1: Replace the current two-command local startup block**

Document four parts under `## 本地启动`:

1. A recommended first-time initialization command that installs CPython 3.12.14 under `%LOCALAPPDATA%/yu-ai-code-mother/python`, locates the real patch-version executable, creates `.venv`, and synchronizes locked dependencies.
2. A normal startup command that locates the same executable, sets `PYTHONPATH` to `.venv/Lib/site-packages` and `src`, and starts `ai_service.app:create_app` through `python -m uvicorn`.
3. The original `uv sync --frozen --python 3.12` and `uv run uvicorn ...` commands as a simplified alternative when uv's managed links work normally.
4. A troubleshooting note for `No Python at ...` and `Missing expected target directory for Python minor version link`, directing users back to the recommended initialization flow.

State that Uvicorn must be stopped before initialization because Windows locks files in `.venv` and causes `uv venv --clear` to fail with access denied while the service is running.

Use this initialization command exactly:

```powershell
$runtimeDir = "$env:LOCALAPPDATA/yu-ai-code-mother/python"
uv python install 3.12.14 --install-dir "$runtimeDir" --no-bin --force
if ($LASTEXITCODE -ne 0) { throw "Python 3.12 installation failed" }

$python = (Get-ChildItem "$runtimeDir/cpython-3.12.14-windows*/python.exe" | Select-Object -First 1).FullName
if (-not $python) { throw "Python 3.12 executable not found" }

uv venv --clear --python "$python" .venv
if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }

uv sync --frozen --python "$python" --link-mode copy
if ($LASTEXITCODE -ne 0) { throw "Dependency synchronization failed" }
```

Use this normal startup command exactly:

```powershell
$runtimeDir = "$env:LOCALAPPDATA/yu-ai-code-mother/python"
$python = (Get-ChildItem "$runtimeDir/cpython-3.12.14-windows*/python.exe" | Select-Object -First 1).FullName
if (-not $python) { throw "Python 3.12 executable not found; run the initialization steps first" }

$env:PYTHONPATH = "$(Resolve-Path './.venv/Lib/site-packages');$(Resolve-Path './src')"
& "$python" -m uvicorn ai_service.app:create_app --factory --host 0.0.0.0 --port 8000
```

- [x] **Step 2: Inspect the updated section for command corruption**

Run:

```powershell
rg -n -A 90 "^## 本地启动" ai-service/README.md
```

Expected: the output contains `ai_service.app:create_app`, contains no `ai\_service` or `create\_app`, and preserves the following `## Docker 启动` section.

- [x] **Step 3: Run documentation checks**

Run:

```powershell
git diff --check
git status --short
```

Expected: `git diff --check` exits with code 0; Git status lists only the plan, `ai-service/README.md`, and the pre-existing untracked `projects/` directory.

- [x] **Step 4: Commit the documentation update**

```powershell
git add docs/superpowers/plans/2026-09-20-ai-service-local-startup.md ai-service/README.md
git commit -m "docs: 修正 AI 服务本地启动说明"
```
