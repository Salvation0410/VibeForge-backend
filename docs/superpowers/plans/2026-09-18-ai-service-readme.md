# AI 服务中文 README 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `ai-service/README.md` 改写为与当前代码一致、可独立指导开发和启动的完整中文使用手册。

**Architecture:** README 以应用工厂和四类职责包为结构入口，说明 Python 与 Spring 的边界、LangGraph 执行流程和内部接口。快速操作保留在 README，复杂联调与排障通过相对链接指向仓库 `doc` 目录，避免重复维护。

**Tech Stack:** Markdown、Python 3.12、FastAPI、LangChain、LangGraph、uv、Redis、Docker

---

### Task 1: 重写中文 README

**Files:**
- Modify: `ai-service/README.md`

- [ ] **Step 1: 更新服务简介与职责边界**

说明 Python 负责模型调用、LangGraph 编排、质量检查和工具决策；Spring 负责用户鉴权、数据库、文件、构建、部署与外部 SSE。

- [ ] **Step 2: 增加目录结构与工作流**

目录树必须包含 `api`、`orchestration`、`models`、`infrastructure`，工作流必须明确三类生成分支、Vue 工具循环、质量检查和最多两次修复。

- [ ] **Step 3: 增加配置表**

逐项覆盖 `.env.example` 中的 12 个 `AI_SERVICE_*` 配置，说明用途和示例或默认值，不写入真实密钥。

- [ ] **Step 4: 增加启动与检查命令**

提供 `uv sync --frozen --python 3.12`、Uvicorn、Docker、健康检查、`pytest`、`compileall` 和 `uv lock --check` 命令。

- [ ] **Step 5: 增加接口、事件、降级和安全说明**

接口路径以 `api/routes.py` 为准；事件类型以 `api/schemas.py` 为准；说明 Redis required/optional 行为、Bearer 鉴权和 Spring 文件边界。

- [ ] **Step 6: 增加相关文档链接**

从 `ai-service/README.md` 使用 `../doc/ai-service-startup.md` 和 `../doc/ai-service-langchain-langgraph-refactor-design.md` 相对链接。

### Task 2: 验证 README 与项目一致

**Files:**
- Verify: `ai-service/README.md`
- Reference: `ai-service/.env.example`
- Reference: `ai-service/src/ai_service/api/routes.py`
- Reference: `ai-service/src/ai_service/api/schemas.py`

- [ ] **Step 1: 核对配置项**

Run:

```powershell
rg "^AI_SERVICE_" ai-service/.env.example
rg "AI_SERVICE_" ai-service/README.md
```

Expected: `.env.example` 的每个配置键都出现在 README。

- [ ] **Step 2: 核对接口路径**

Run:

```powershell
rg '"/(internal/v1|health)' ai-service/src/ai_service/api/routes.py
```

Expected: README 覆盖健康、路由、流式生成和取消接口。

- [ ] **Step 3: 运行文档中的项目验证命令**

Run from `ai-service`:

```powershell
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Expected: 语法检查成功、全部测试通过、锁文件有效。

- [ ] **Step 4: 检查 Markdown 差异**

Run: `git diff --check`

Expected: 无空白错误。

- [ ] **Step 5: 中文提交**

```powershell
git add ai-service/README.md
git commit -m "docs: 完善 AI 服务中文使用说明" -m "补充架构边界、源码目录、工作流、配置、启动、接口、测试、降级和安全说明。"
```
