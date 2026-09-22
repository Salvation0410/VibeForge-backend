# Python 提示词语义迁移设计

## 目标

将 Legacy Java 链路中与路由、三类代码生成、质量检查和修复有关的关键提示语义迁移到 Python `OpenAICompatibleModel`，使 LangGraph 链路具备明确的输出协议、修改范围和质量约束，同时保持 Java 业务执行边界与 Legacy 回滚路径不变。

本轮只完成交接文档 `13.6` 的第 1 步。当前活动产物上下文、Vue 工具循环增强、构建失败修复、真实端到端验证和灰度调整均不在本轮实现范围内，将在迁移验证后形成后续实施计划。

## 方案选择

采用按职责集中组织的 Python 提示词模块：

```text
ai-service/src/ai_service/prompts/
  __init__.py
  routing.py
  generation.py
  review.py
```

`routing.py` 负责生成类型路由；`generation.py` 负责 HTML、MULTI_FILE 和 VUE_PROJECT 三类生成；`review.py` 负责质量检查和修复。当前提示词规模较小，按职责聚合比每种行为单独建文件更容易维护，也不引入运行时文本文件加载和缺失资源处理。

## 提示词职责

### 路由

路由提示词必须限定输出为 `HTML`、`MULTI_FILE` 或 `VUE_PROJECT` 中的一个大写标识，不允许解释、标点、Markdown 或多个候选值。现有 `route()` 的解析和兜底行为保持不变。

### HTML 生成

HTML 提示词要求返回唯一、完整、可直接校验的 HTML Markdown 代码块，包含完整文档结构，并禁止代码块外解释。二次修改语义要求保留用户未要求改变的功能、文字、图片和操作方式。

### MULTI_FILE 生成

多文件提示词要求严格按固定顺序返回 `index.html`、`style.css` 和 `script.js` 三个完整 Markdown 代码块，不允许额外文件、空内容或围栏外解释。确定性解析和发布仍由 Spring 执行。

### Vue 生成

Vue 提示词适配 Python 当前的 JSON 工具调用协议，不照搬 Legacy Java 的 LangChain4j 工具描述。模型只能通过现有 `vue_tool_prompt()` 获得标准工具名称、参数和返回结构；可信的 `appId` 与 `codeGenType` 仍由 Python 注入，真实文件操作继续由 Spring 工具网关执行。

### 质量检查与修复

质量检查提示词只允许返回 `PASS` 或 `REPAIR`，用于判断需求与功能质量，不能替代 Spring 的解析、确定性校验、浏览器烟测或项目构建。

修复提示词要求结合当前完整产物以及校验、构建和质量上下文，返回符合当前生成类型协议的完整候选产物，不返回补丁、解释或残缺片段。Vue 修复仍必须通过文件工具完成，不能绕过 Spring 输出或操作项目文件。

## 模型适配器接入

`OpenAICompatibleModel` 的四个入口分别使用对应提示词：

- `route()` 使用路由提示词。
- `generate()` 根据分支选择生成提示词；Vue 分支额外追加现有工具契约。
- `review()` 使用质量检查提示词，并保留现有结果规范化逻辑。
- `repair()` 使用修复提示词，并保留传入产物、校验和构建上下文的现有接口。

本轮不改变 `ModelProtocol`、LangGraph 状态结构、节点顺序、事件协议、修复次数、工具白名单或 Spring/Python HTTP 契约。

## 错误与兼容性

未知生成分支必须在选择提示词时抛出明确的 `ValueError`，避免静默使用错误协议。模型返回格式错误时继续由现有路由解析、工具校验和工作流失败路径处理。

Java 资源目录下的 Legacy 提示词继续保留，`legacy`、`langgraph` 和灰度路由配置均不改变。Python 不读取 MySQL，不挂载项目目录，也不获得文件、构建或发布的执行权。

## 测试与验收

单元测试分为两层：

1. 提示词内容测试：验证三种生成类型、严格输出格式、保留未修改内容、质量检查和完整修复等关键约束，并验证未知分支被拒绝。
2. 模型调用测试：使用假模型客户端捕获 `ainvoke()` 消息，验证 `route()`、三个 `generate()` 分支、`review()` 和 `repair()` 实际收到正确的 `SystemMessage`，Vue 分支同时包含工具契约。

完成标准：

- `uv run python -m compileall -q src` 通过。
- `uv run pytest` 全部通过。
- `uv lock --check` 通过。
- `git diff --check` 不报告空白错误。
- 不声称真实模型、真实 Redis 或完整端到端生成通过。

## 后续计划边界

提示词迁移验证完成后，另行编写实施计划，依次覆盖：当前活动产物或项目上下文、Vue 工具循环完善、构建失败进入修复、Legacy/LangGraph 结果对比、真实 Spring HTTP 与 Redis 验证、三类型端到端验收及灰度提升。该计划经用户确认后再执行。
