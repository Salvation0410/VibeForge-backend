# AI 服务职责分包与中文注释实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Python AI 服务按 API、编排、模型和基础设施职责拆包，同步全部 import 与测试，并为关键方法补充中文 docstring。

**Architecture:** 保留 `ai_service.app:create_app` 作为组合根和启动入口。HTTP 模型与路由进入 `api`，LangGraph 状态和执行逻辑进入 `orchestration`，模型协议与实现进入 `models`，Redis 与 Spring 网关进入 `infrastructure`；包之间只通过现有接口协作，不修改协议和业务行为。

**Tech Stack:** Python 3.12、FastAPI、LangChain、LangGraph、Redis、httpx、pytest、uv

---

### Task 1: 创建职责包并迁移模块

**Files:**
- Create: `ai-service/src/ai_service/api/__init__.py`
- Create: `ai-service/src/ai_service/orchestration/__init__.py`
- Create: `ai-service/src/ai_service/models/__init__.py`
- Create: `ai-service/src/ai_service/infrastructure/__init__.py`
- Move: `models.py` -> `api/schemas.py`
- Move: `workflow.py` -> `orchestration/workflow.py`
- Move: `events.py` -> `orchestration/events.py`
- Move: `cancellation.py` -> `orchestration/cancellation.py`
- Split: `llm.py` -> `models/base.py`, `models/openai_compatible.py`
- Move: `checkpoint.py` -> `infrastructure/checkpoint.py`
- Move: `tools.py` -> `infrastructure/spring_tools.py`

- [ ] **Step 1: 创建四个职责包和 `__init__.py`**

每个包只导出稳定公共类型，不执行运行期初始化。

- [ ] **Step 2: 使用 `git mv` 迁移无需拆分的模块**

保留 Git 文件历史，移动后立即修正模块内部绝对 import。

- [ ] **Step 3: 拆分模型协议和 OpenAI 兼容实现**

`base.py` 保留 `ToolCall`、`ModelTurn`、`GenerationModel`；`openai_compatible.py` 保留 `OpenAICompatibleModel` 和 JSON fence 清理函数。

- [ ] **Step 4: 搜索旧模块 import**

Run:

```powershell
rg -n "ai_service\.(workflow|events|cancellation|llm|models|checkpoint|tools)" ai-service
```

Expected: 仅计划或说明文档可能出现，`src` 与 `tests` 中无旧 import。

### Task 2: 拆分 API 路由并保持应用入口兼容

**Files:**
- Modify: `ai-service/src/ai_service/app.py`
- Create: `ai-service/src/ai_service/api/dependencies.py`
- Create: `ai-service/src/ai_service/api/routes.py`

- [ ] **Step 1: 将 Bearer 校验封装为依赖工厂**

依赖工厂接收服务令牌，返回 FastAPI header dependency，并继续使用常量时间比较。

- [ ] **Step 2: 将健康、路由、生成和取消接口提取到路由注册函数**

注册函数接收模型、工作流、checkpoint 和取消注册表；流式生成继续输出逐行 JSON，断连后继续标记取消。

- [ ] **Step 3: 精简应用工厂**

`create_app` 只加载配置、创建依赖、管理生命周期、写入 `app.state` 并调用路由注册函数。启动命令仍为：

```powershell
uv run uvicorn ai_service.app:create_app --factory --host 0.0.0.0 --port 8000
```

### Task 3: 补充必要中文注释

**Files:**
- Modify: `ai-service/src/ai_service/app.py`
- Modify: `ai-service/src/ai_service/config.py`
- Modify: `ai-service/src/ai_service/api/*.py`
- Modify: `ai-service/src/ai_service/orchestration/*.py`
- Modify: `ai-service/src/ai_service/models/*.py`
- Modify: `ai-service/src/ai_service/infrastructure/*.py`

- [ ] **Step 1: 为类和公共方法补充中文 docstring**

说明职责、关键参数、返回结果、副作用和失败边界，不重复类型标注。

- [ ] **Step 2: 为复杂编排补充少量行内注释**

只解释节点统一包装、Vue 工具循环、修复回环上限和 checkpoint 键结构。

- [ ] **Step 3: 检查注释准确性**

逐个对照实现，删除任何描述尚未实现能力、固定版本号或固定部署地址的注释。

### Task 4: 同步测试和中文启动文档

**Files:**
- Modify: `ai-service/tests/conftest.py`
- Modify: `ai-service/tests/test_gateway_and_config.py`
- Modify: `doc/ai-service-startup.md`

- [ ] **Step 1: 更新测试 import**

测试分别从 `models.base`、`infrastructure.checkpoint` 和 `infrastructure.spring_tools` 导入类型。

- [ ] **Step 2: 增加包结构导入测试**

验证 `ai_service.app:create_app` 可导入，并确认主要新模块均可加载。

- [ ] **Step 3: 更新启动文档**

增加中文“源码目录说明”章节，解释四类包的职责，同时保持命令和配置名称原样。

### Task 5: 验证并提交

**Files:**
- Verify: `ai-service/src/`
- Verify: `ai-service/tests/`
- Verify: `doc/ai-service-startup.md`

- [ ] **Step 1: 运行语法检查**

Run: `uv run python -m compileall src`

Expected: 所有模块编译成功。

- [ ] **Step 2: 运行 Python 测试**

Run: `uv run pytest`

Expected: 全部测试通过。

- [ ] **Step 3: 检查锁文件**

Run: `uv lock --check`

Expected: 锁文件有效且不发生依赖变更。

- [ ] **Step 4: 检查差异和工作树**

Run: `git diff --check`，随后确认除用户已有 `projects/` 外只包含本任务文件。

- [ ] **Step 5: 使用中文说明提交**

```powershell
git commit -m "refactor: 按职责拆分 AI 服务模块" -m "将接口、编排、模型和基础设施代码迁移到独立包，同步更新 import、测试、中文方法注释与启动文档。"
```
