# P0 验收加固与 LangSmith 旁路追踪 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 P0 真实验收准备和路由错误脱敏，并使用现有 `.env` 的 LangSmith 配置接入默认可关闭、脱敏且不影响业务终态的旁路追踪。

**Architecture:** Python 配置层读取已有 `LANGSMITH_TRACING`、`LANGSMITH_ENDPOINT`、`LANGSMITH_API_KEY`、`LANGSMITH_PROJECT`，工作流通过一个小型 tracing 适配器提交白名单元数据；追踪异常被隔离。Spring/Python/Vue 的 P0 验收仍通过现有 opt-in 脚本和脱敏记录完成。

**Tech Stack:** Python 3.12, FastAPI, LangGraph, LangSmith SDK, Pydantic Settings, pytest, Spring Boot 3.5, PowerShell 验收脚本。

---

### Task 1: 修复路由错误脱敏并补回归测试

**Files:**
- Modify: `ai-service/src/ai_service/api/routes.py`
- Test: `ai-service/tests/test_gateway_and_config.py`

- [ ] **Step 1: Add a test asserting unknown route output is stable and redacted**

测试模型返回包含敏感文本的未知类型，断言 HTTP 502 的响应只包含稳定错误码，不包含原始文本。

- [ ] **Step 2: Run the focused test and observe failure**

Run: `uv run pytest tests/test_gateway_and_config.py -k route -q`

- [ ] **Step 3: Return a fixed error detail and log only a bounded summary**

在 `routes.py` 中将异常响应改为固定 `MODEL_ROUTE_INVALID`，原始类型不进入响应；日志只记录长度受限且脱敏的类型类别。

- [ ] **Step 4: Run the focused test and full Python tests**

Run: `uv run pytest tests/test_gateway_and_config.py -k route -q` and `uv run pytest`

- [ ] **Step 5: Commit**

`git commit -m "fix(ai): 脱敏模型路由错误"`

### Task 2: Add `.env`-driven LangSmith tracing adapter

**Files:**
- Modify: `ai-service/src/ai_service/config.py`
- Create: `ai-service/src/ai_service/infrastructure/langsmith_tracing.py`
- Modify: `ai-service/src/ai_service/app.py`
- Modify: `ai-service/src/ai_service/orchestration/workflow.py`
- Test: `ai-service/tests/test_langsmith_tracing.py`

- [ ] **Step 1: Add config fields mapped to existing variables**

读取 `LANGSMITH_TRACING`、`LANGSMITH_ENDPOINT`、`LANGSMITH_API_KEY`、`LANGSMITH_PROJECT`；默认不开启，缺少凭据时保持关闭并记录脱敏警告。

- [ ] **Step 2: Write adapter tests**

覆盖关闭时不创建客户端、开启时仅允许白名单 metadata/tags、prompt/artifact/tool 参数被过滤、发送异常不抛出到工作流。

- [ ] **Step 3: Implement adapter**

使用已锁定的 `langsmith` SDK 创建可选 client；只提交 run 名称、节点、稳定 ID、计数、耗时和错误码；所有输入先经过白名单和长度限制。关闭或 SDK 不可用时使用空实现。

- [ ] **Step 4: Attach workflow lifecycle metadata**

在工作流开始、节点完成和终态处调用适配器；追踪失败只写脱敏日志，不能改变 checkpoint、发布或取消路径。

- [ ] **Step 5: Run Python checks**

Run: `uv run pytest tests/test_langsmith_tracing.py -q`, `uv run python -m compileall -q src`, `uv lock --check`, `uv run pytest`

### Task 3: Update design and handoff documentation

**Files:**
- Modify: `docs/superpowers/specs/2026-10-06-p0-langsmith-observability-design.md`
- Modify: `doc/ai-service-phase-one-handoff.md`
- Modify: `ai-service/README.md`
- Modify: `doc/ai-service-startup.md`

- [ ] **Step 1: Document existing `.env` variables**

明确 LangSmith 开关和凭据来自现有 `.env`，不新增独立配置；默认关闭，开启后仍执行代码层脱敏。

- [ ] **Step 2: Document P0 execution gates and evidence**

增加 PostgreSQL、Redis、端到端和 LangSmith 脱敏验证步骤，保留未执行项的真实状态。

- [ ] **Step 3: Run documentation whitespace checks**

Run: `git diff --check`

### Task 4: Update AGENTS.md and commit task-related pending work

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add Chinese Conventional Commit policy**

要求提交信息正文使用中文、类型使用 Conventional Commits，示例 `feat(ai): 接入LangSmith脱敏追踪`，提交前运行 `git log -1` 和格式检查。

- [ ] **Step 2: Inspect pending files**

只纳入本次任务和上次用户要求提交的计划文档；保留 `projects/` 和无关 `.gitignore` 改动。

- [ ] **Step 3: Run final verification**

Run Python checks、相关 Java 测试、`git diff --check`、后端和前端 `git status --short --branch`。

- [ ] **Step 4: Commit with a Chinese Conventional Commit message**

使用中文提交信息，例如 `feat(ai): 接入LangSmith并完善P0验收`。
