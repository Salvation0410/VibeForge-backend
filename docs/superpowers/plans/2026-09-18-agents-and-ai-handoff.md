# AGENTS 指南与 AI 重构交接文档实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 更新项目协作指南并生成第一阶段 AI 服务重构交接文档，让下一轮 AI 能从仓库实际状态继续工作。

**Architecture:** `AGENTS.md` 作为全仓库长期协作规则，描述双服务架构、开发约束和验证标准；`doc/ai-service-phase-one-handoff.md` 作为阶段性快照，记录完成内容、关键文件、提交、限制和后续优先级。两份文档均以当前源码和新鲜验证结果为事实来源。

**Tech Stack:** Markdown、Spring Boot 3.5.4、Java 21、Python 3.12、FastAPI、LangChain、LangGraph、uv、Redis、Maven

---

### Task 1: 全面更新 AGENTS.md

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: 更新项目概览和技术栈**

将项目描述为 Spring Boot 业务后端加 Python AI 服务，补充 FastAPI、LangChain、LangGraph、uv 和两个 Redis database。

- [ ] **Step 2: 更新命令和目录结构**

加入 AI 服务安装、启动、测试、compileall、锁文件校验，以及四类 Python 职责包和 Java 网关目录。

- [ ] **Step 3: 更新 AI 请求链与编码约束**

描述 `AiGenerationGateway`、Legacy/LangGraph/gray、NDJSON、Spring 工具边界和 SSE 兼容，明确 LangGraph4j 不是当前主链路。

- [ ] **Step 4: 更新配置安全、测试和交付检查**

说明两个 Python Bearer 配置与 Spring `ai.token` 的当前关系、`.env` 规则、跨服务测试和不能提交 `projects/` 等生成目录。

### Task 2: 编写第一阶段 AI 重构交接文档

**Files:**
- Create: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: 记录阶段目标、当前架构和请求流**

区分 Spring、Python、Redis 和模型供应商职责，列出对外 SSE 与内部 NDJSON 数据流。

- [ ] **Step 2: 建立关键文件索引**

覆盖 Java 网关、工具控制器、应用服务接入、Python API、编排、模型、基础设施、配置、测试和现有文档。

- [ ] **Step 3: 记录配置、启动顺序和验证命令**

说明令牌生成与三处一致关系、Redis database 2、Python 先启动还是 Spring 先启动均可但联调前双方必须就绪，以及健康检查方法。

- [ ] **Step 4: 记录提交、限制、风险和后续任务**

列出第一阶段核心提交；明确进程内工具幂等、同步路由调用、静态令牌、协作式取消、无断线恢复和未完成真实模型端到端验证等限制。

- [ ] **Step 5: 添加下一轮 AI 检查清单**

要求先读 `AGENTS.md`、README、设计与交接文档，再检查 Git 状态、配置、测试和敏感信息，保留 `projects/` 未跟踪文件。

### Task 3: 验证文档事实并提交

**Files:**
- Verify: `AGENTS.md`
- Verify: `doc/ai-service-phase-one-handoff.md`

- [ ] **Step 1: 运行 Python 验证**

Run from `ai-service`:

```powershell
uv run pytest
uv lock --check
```

Expected: 全部测试通过，锁文件有效。

- [ ] **Step 2: 运行 Java 干净编译**

Run from repository root: `mvn clean -DskipTests compile`

Expected: `BUILD SUCCESS`。

- [ ] **Step 3: 核对文件、提交和链接**

使用 `Test-Path`、`git log --oneline` 和 `rg` 验证文档引用存在，提交哈希与标题准确。

- [ ] **Step 4: 检查差异范围**

Run: `git diff --check` 和 `git status --short`。

Expected: 除用户已有 `projects/` 外，只包含两个目标文档。

- [ ] **Step 5: 分别使用中文说明提交**

```powershell
git commit -m "docs: 更新项目协作指南" -m "同步 Spring 与 Python AI 双服务架构、开发命令、配置安全、测试策略和交付检查。"
git commit -m "docs: 增加第一阶段 AI 重构交接说明" -m "记录当前架构、关键文件、配置启动、验证结果、核心提交、已知限制和下一阶段建议。"
```
