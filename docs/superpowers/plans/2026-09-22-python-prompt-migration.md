# Python Prompt Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Legacy Java 的路由、三类生成、质量检查和修复语义迁移到 Python 模型适配器，并用内容测试和真实消息捕获测试锁定调用契约。

**Architecture:** 在 `ai_service.prompts` 中按路由、生成、审查三个职责集中维护系统提示词，由 `OpenAICompatibleModel` 的四个公开模型入口选择并传入对应 `SystemMessage`。Vue 分支继续追加版本化工具契约；Java Legacy 提示词、LangGraph 工作流结构和 Spring 业务执行边界保持不变。

**Tech Stack:** Python 3.12、LangChain Core、LangChain OpenAI、pytest、pytest-asyncio。

---

### Task 1: 锁定提示词内容契约

**Files:**
- Create: `ai-service/tests/test_prompts.py`
- Create: `ai-service/src/ai_service/prompts/__init__.py`
- Create: `ai-service/src/ai_service/prompts/routing.py`
- Create: `ai-service/src/ai_service/prompts/generation.py`
- Create: `ai-service/src/ai_service/prompts/review.py`

- [ ] **Step 1: 编写失败的提示词内容测试**

测试必须覆盖：三个严格路由值；HTML 唯一完整代码块；MULTI_FILE 三文件固定顺序；Vue JSON 工具协议与受控参数；质量检查只返回 `PASS`/`REPAIR`；修复返回完整候选；未知分支抛出 `ValueError`。

```python
def test_unknown_generation_branch_is_rejected():
    with pytest.raises(ValueError, match="Unsupported generation branch"):
        generation_system_prompt("UNKNOWN")
```

- [ ] **Step 2: 运行测试并确认缺少提示词模块时失败**

Run: `Set-Location ai-service; uv run pytest tests/test_prompts.py -q`

Expected: 新模块尚未实现时，收集阶段因 `ai_service.prompts` 不存在而失败。

- [ ] **Step 3: 实现按职责划分的提示词模块**

`generation_system_prompt()` 必须使用显式分支映射，并拒绝未知类型：

```python
def generation_system_prompt(branch: str) -> str:
    prompts = {
        "HTML": HTML_SYSTEM_PROMPT,
        "MULTI_FILE": MULTI_FILE_SYSTEM_PROMPT,
        "VUE_PROJECT": VUE_PROJECT_SYSTEM_PROMPT,
    }
    try:
        return prompts[branch]
    except KeyError as exc:
        raise ValueError(f"Unsupported generation branch: {branch}") from exc
```

提示词内容按设计文档约束输出格式、修改范围、Spring 执行边界及完整修复语义；`__init__.py` 只导出模型适配器需要的四个符号。

- [ ] **Step 4: 运行提示词内容测试**

Run: `Set-Location ai-service; uv run pytest tests/test_prompts.py -q`

Expected: `6 passed`。

- [ ] **Step 5: 提交提示词模块与内容测试**

```powershell
git add -- ai-service/src/ai_service/prompts ai-service/tests/test_prompts.py
git commit -m "feat: 迁移 Python 代码生成提示词"
```

### Task 2: 接入模型适配器并锁定实际消息

**Files:**
- Modify: `ai-service/src/ai_service/models/openai_compatible.py`
- Modify: `ai-service/tests/test_openai_compatible.py`

- [ ] **Step 1: 扩展捕获客户端与调用级测试**

让捕获客户端支持按测试指定响应，并增加以下断言：

```python
@pytest.mark.asyncio
async def test_route_uses_routing_system_prompt():
    model = model_with_response("html")
    assert await model.route("做一个官网") == "HTML"
    assert model._client.messages[0].content == ROUTING_SYSTEM_PROMPT


@pytest.mark.asyncio
@pytest.mark.parametrize("branch", ["HTML", "MULTI_FILE"])
async def test_static_generation_uses_branch_system_prompt(branch):
    model = model_with_response("artifact")
    await model.generate(branch, {"prompt": "build it"})
    assert model._client.messages[0].content == generation_system_prompt(branch)


@pytest.mark.asyncio
async def test_review_and_repair_use_their_system_prompts():
    review_model = model_with_response("PASS")
    assert await review_model.review("artifact", {"prompt": "check"}) is True
    assert review_model._client.messages[0].content == QUALITY_REVIEW_SYSTEM_PROMPT

    repair_model = model_with_response("fixed")
    result = await repair_model.repair("artifact", {"validation": {"valid": False}})
    assert result.content == "fixed"
    assert repair_model._client.messages[0].content == REPAIR_SYSTEM_PROMPT
```

保留现有 Vue 工具白名单断言，并新增 Vue 系统消息同时包含 `generation_system_prompt("VUE_PROJECT")` 和 `vue_tool_prompt()` 的断言。

- [ ] **Step 2: 运行调用级测试并确认旧适配器失败**

Run: `Set-Location ai-service; uv run pytest tests/test_openai_compatible.py -q`

Expected: 路由、静态生成、审查和修复的系统消息仍是旧短提示词，因此新增断言失败。

- [ ] **Step 3: 将提示词接入四个模型入口**

接入方式：

```python
generation_instructions = generation_system_prompt(branch)
if branch == "VUE_PROJECT":
    generation_instructions = f"{generation_instructions}\n\n{vue_tool_prompt()}"
```

`route()` 使用 `ROUTING_SYSTEM_PROMPT`，`review()` 使用 `QUALITY_REVIEW_SYSTEM_PROMPT`，`repair()` 使用 `REPAIR_SYSTEM_PROMPT`。不改变用户消息 JSON、Vue 返回解析、finish reason 或 token usage 逻辑。

- [ ] **Step 4: 运行模型适配器与提示词测试**

Run: `Set-Location ai-service; uv run pytest tests/test_prompts.py tests/test_openai_compatible.py -q`

Expected: 所有测试通过。

- [ ] **Step 5: 提交适配器接入与调用级测试**

```powershell
git add -- ai-service/src/ai_service/models/openai_compatible.py ai-service/tests/test_openai_compatible.py
git commit -m "feat: 接入 Python 系统提示词"
```

### Task 3: 完整验证与交付检查

**Files:**
- Verify only; no production files added in this task.

- [ ] **Step 1: 编译 Python 源码**

Run: `Set-Location ai-service; uv run python -m compileall -q src`

Expected: exit code `0`，无编译错误。

- [ ] **Step 2: 运行完整 Python 测试**

Run: `Set-Location ai-service; uv run pytest`

Expected: 全部测试通过；允许记录既有依赖弃用警告，但不得忽略失败或错误。

- [ ] **Step 3: 校验锁文件**

Run: `Set-Location ai-service; uv lock --check`

Expected: `Resolved ... packages` 或等价成功输出，exit code `0`，且不改写 `uv.lock`。

- [ ] **Step 4: 检查空白错误和任务范围**

Run: `git diff --check`

Expected: 无输出，exit code `0`。

Run: `git status --short`

Expected: 提示词迁移相关文件已提交；`.gitignore`、`workflow.py`、交接文档、另一份 HTTP 计划和 `projects/` 等既有用户改动仍被保留且未混入迁移提交。

- [ ] **Step 5: 记录验证边界**

交付说明必须明确：本轮验证不调用真实模型、不连接真实 Redis、不启动 Spring/Python 服务，也不代表三种生成类型的端到端验收已经通过。
