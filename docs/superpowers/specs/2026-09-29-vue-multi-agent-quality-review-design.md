# Vue 多 Agent 质量审查设计

**日期：** 2026-09-29

**状态：** 已实施，自动测试通过，真实环境人工验证待完成

**适用范围：** Python AI 服务的 `VUE_PROJECT` 质量审查节点

## 1. 背景

当前 LangGraph 工作流在产物校验和 Vue 项目构建完成后调用单一质量审查模型。模型协议 `GenerationModel.review()` 只返回布尔值，OpenAI 兼容实现只识别 `PASS` 或 `REPAIR`。当审查结果为 `REPAIR` 时，工作流只知道质量未通过，却没有具体问题、证据和修复要求可以传给后续 `repair` 节点。

因此，确定性校验和项目构建虽然能够发现格式、解析和编译问题，但对于需求遗漏、交互不可用、功能行为不完整等质量问题，现有修复节点缺少明确上下文，容易产生盲修或无效修复。

本设计仅在 `VUE_PROJECT` 的质量审查阶段引入运行时多 Agent 协作。多 Agent 只负责读取有界源码快照并输出结构化审查意见，不能直接操作文件、调用 Spring 工具或发布产物。

## 2. 已确认决策

- 采用三个并行 Reviewer 和一个确定性 Python 聚合器，不增加模型 Judge。
- 第一阶段仅覆盖 `VUE_PROJECT`；HTML 和 MULTI_FILE 继续使用现有单 Reviewer。
- 任一 Reviewer 超时、调用异常或结构化输出非法时，本次生成失败，不使用部分结果，不回退单 Reviewer，也不进入 repair。
- `critical` 和 `major` 问题触发 repair；`minor` 问题只参与本次审查调用内的聚合，不进入持久化工作流状态，也不消耗修复次数。
- 首次 Vue 生成以及每次修复后，都审查 Spring 返回的真实、有界源码快照。
- Reviewer 只读，不获得 Spring 工具网关或文件写入能力。
- 保持最多两次修复的现有限制。
- 不启用长期记忆，不引入 `PostgresStore`。
- 不改变公开 SSE 事件类型、Java 网关协议或前端行为。

## 3. 目标与非目标

### 3.1 目标

1. 将 Vue 质量审查拆分为需求完整性、功能交互和技术质量三个互补角色。
2. 通过并行调用控制相对串行调用的额外延迟。
3. 使用严格结构化结果和确定性规则完成裁决。
4. 将有界、可执行的阻断问题反馈传给现有 repair 节点。
5. 保持 Spring 对项目文件、构建、发布和业务数据的唯一所有权。
6. 保持取消、checkpoint、修复上限和对外事件协议的现有语义。

### 3.2 非目标

本轮不实施以下内容：

- HTML 或 MULTI_FILE 多 Agent 审查。
- 第四个模型 Judge。
- Reviewer 自动重试或动态角色创建。
- Reviewer 直接调用 Spring 工具。
- 多个 Repair Agent 并行修改文件。
- 长期记忆、`PostgresStore` 或跨请求学习。
- PostgreSQL checkpoint schema 改造。
- 新增公开 SSE 事件或向前端展示内部审查问题。
- 百分比灰度或用户维度分桶。
- 修改最多两次修复的限制。

## 4. 总体架构

```text
Vue 生成或修复
    ↓
确定性产物校验
    ↓
Spring 项目构建
    ↓
Spring vue_source_snapshot 返回有界真实源码
    ↓
并行多 Agent 质量审查
    ├─ Requirement Reviewer
    ├─ Function Reviewer
    └─ Technical Reviewer
    ↓
结构化结果校验
    ↓
确定性聚合器
    ├─ Reviewer 系统异常 → failed
    ├─ 存在 critical/major → repair
    └─ 仅 minor 或无问题 → completed
```

多 Agent 审查不替代确定性产物校验和项目构建。只有硬校验有效且 Vue 构建成功的候选，才进入质量审查。

## 5. Reviewer 职责

### 5.1 Requirement Reviewer

检查产物是否满足用户需求，包括页面、功能、文字、图片和操作方式是否完整。对于二次修改，重点检查是否误删或破坏原有能力。

该角色不评价纯粹的代码风格，也不因主观视觉偏好产生阻断问题。

### 5.2 Function Reviewer

检查按钮、链接、表单、导航、状态反馈和核心业务流程是否具备合理、可执行的行为。重点发现能够成功构建但用户无法正常操作的问题。

该角色不负责评判内部代码组织是否优雅。

### 5.3 Technical Reviewer

检查明显运行风险、Vue 使用方式、组件结构、资源引用、错误状态和构建本身无法覆盖的技术缺陷。

该角色不能仅因个人重构偏好、命名风格或非必要架构调整产生阻断问题。

### 5.4 公共边界

三个 Reviewer：

- 接收相同的用户需求、生成类型、构建摘要和有界源码快照。
- 使用独立 system prompt。
- 不共享可变对话状态。
- 不持有 Spring 工具网关。
- 不直接修改 LangGraph 工作流状态。
- 只返回结构化 `ReviewerResult`。

## 6. 源码审查输入

启用多 Agent 后，每次成功构建的 Vue 候选都调用现有 `vue_source_snapshot` 工具：

```text
首次生成构建成功 → repairCount=0 的快照 → 三个 Reviewer
第一次修复构建成功 → repairCount=1 的新快照 → 三个 Reviewer
第二次修复构建成功 → repairCount=2 的新快照 → 三个 Reviewer
```

工具调用 ID 保持稳定：

```text
{requestId}:vue-source-snapshot:{repairCount}
```

Python 不直接读取项目目录。源码快照仍由 Spring 在应用沙箱中生成，并遵循现有工具契约的文件数量、单文件内容和总内容边界。

源码和文件内容不能进入 NDJSON/SSE 事件。工具事件只暴露文件数量、遗漏数量和是否截断等统计信息。

## 7. 结构化数据契约

建议使用以下领域模型：

```python
class ReviewerRole(StrEnum):
    REQUIREMENT = "requirement"
    FUNCTION = "function"
    TECHNICAL = "technical"


class IssueSeverity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class QualityIssue(BaseModel):
    code: str
    category: str
    severity: IssueSeverity
    summary: str
    evidence: str
    repair_hint: str


class ReviewerResult(BaseModel):
    reviewer: ReviewerRole
    summary: str
    issues: list[QualityIssue]


class QualityReviewResult(BaseModel):
    passed: bool
    reviewer_results: list[ReviewerResult]
    blocking_issues: list[QualityIssue]
    minor_issues: list[QualityIssue]
    repair_feedback: str
```

模型不直接返回最终 `passed`。是否通过完全由聚合器根据经过校验的问题严重度计算，避免模型同时返回“通过”和 critical 问题等矛盾结果。

## 8. 数据上限

第一阶段采用以下硬限制：

| 字段 | 上限 |
|---|---:|
| 每个 Reviewer 的问题数 | 5 条 |
| 三个 Reviewer 聚合后的问题数 | 12 条 |
| Reviewer summary | 300 字符 |
| issue code | 64 字符 |
| category | 64 字符 |
| issue summary | 300 字符 |
| evidence | 600 字符 |
| repair hint | 600 字符 |
| 最终 repair feedback | 4,000 字符 |

Python 必须验证所有边界，不能只依赖提示词。超限、字段缺失、未知严重度、Reviewer 身份错误或非法 JSON 都属于审查系统异常，按失败策略处理。

## 9. 确定性聚合

聚合器按以下顺序运行：

1. 确认正好收到 requirement、function、technical 三个结果。
2. 校验每个结果声明的 Reviewer 身份与实际调用角色一致。
3. 校验字段类型、枚举、问题数量和文本长度。
4. 以 `code + category + 标准化 summary` 归并重复问题。
5. 同一问题出现不同严重度时保留最高严重度。
6. 将 `critical` 和 `major` 放入 `blocking_issues`。
7. 将 `minor` 放入 `minor_issues`。
8. `blocking_issues` 非空时设置 `passed=false`。
9. 只有 minor 或完全无问题时设置 `passed=true`。
10. 仅根据 `blocking_issues` 生成有界 `repair_feedback`。

聚合器是普通 Python 代码，不调用额外模型。

## 10. Repair 反馈

聚合器使用稳定格式生成反馈，而不是直接拼接 Reviewer 原始响应：

```text
质量审查未通过，请修复以下阻断问题，并保持未提及的现有功能不变。

1. [requirement/major/REQ_MISSING_SEARCH]
问题：用户要求的搜索功能未实现。
证据：页面存在搜索输入框，但没有提交或过滤逻辑。
修复要求：补充搜索触发和结果过滤，同时保留现有列表功能。
```

Repair 上下文增加质量反馈：

```python
repair_context = {
    **state["context"],
    "codeGenType": state["code_gen_type"],
    "repairCount": count,
    "validation": state.get("validation"),
    "build": state.get("build"),
    "qualityReview": {
        "repairFeedback": state.get("repair_feedback", ""),
    },
}
```

minor 问题不进入 `repair_feedback`。修复后必须重新执行确定性校验、项目构建、源码快照和三方审查。

## 11. 并发、超时与失败语义

三个 Reviewer 使用 Python 3.12 `asyncio.TaskGroup` 并行执行。任一任务失败时，TaskGroup 自动取消其余任务，不允许使用部分成功结果继续聚合。

新增配置：

```text
AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED=false
AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS=60
```

第一阶段：

- 功能开关默认关闭。
- 三个 Reviewer 共享 60 秒整体超时。
- 不自动重试。
- 不回退现有单 Reviewer。
- HTML 和 MULTI_FILE 不读取该开关，始终保持现有审查路径。

稳定内部错误码：

```text
MULTI_AGENT_REVIEW_TIMEOUT
MULTI_AGENT_REVIEW_MODEL_ERROR
MULTI_AGENT_REVIEW_INVALID_OUTPUT
MULTI_AGENT_REVIEW_SNAPSHOT_ERROR
```

任一错误均发送现有 `failed` 终态，不进入 repair，不发布候选版本。错误消息不得包含源码、用户完整提示词或模型原始响应。

## 12. 取消语义

现有取消链路继续作为唯一取消入口：

```text
客户端或 Spring 发出取消
    ↓
CancellationRegistry + ActiveGenerationRegistry
    ↓
取消工作流父任务
    ↓
TaskGroup 取消三个 Reviewer
    ↓
不聚合、不 repair、不发布
    ↓
发送 cancelled 终态
```

`asyncio.CancelledError` 必须原样重新抛出，不能包装成模型错误。

工作流在获取源码快照前、启动 Reviewer 前，以及聚合完成后进入 repair 或 completed 前继续检查现有协作式取消状态，防止取消与审查完成竞争时继续执行。

## 13. Checkpoint 语义

`quality_review` 保持单一 LangGraph 节点的原子输出边界：

- 不将单个 Reviewer 的部分结果提前写入工作流状态。
- 三个 Reviewer 和聚合全部成功后才返回精简节点结果：`quality_passed` 与必要时的有界 `repair_feedback`。
- 节点完成后的图 checkpoint 只在存在阻断问题时包含最多 4000 字符的 `repair_feedback`，用于恢复到 repair。
- minor 详情、Reviewer summary、`reviewer_results` 和 `quality_issues` 不进入工作流状态或图 checkpoint。
- 进程在节点执行中退出时，不复用部分 Reviewer 结果；恢复后重新执行三方审查。
- 节点已经完成并成功 checkpoint 后，恢复从 `repair_feedback` 继续进入 repair，不重复调用已经完成的 Reviewer；修复后仍重新执行三方审查。

整组三方审查和 `vue_source_snapshot` 都是只读操作，没有文件副作用，因此重放安全。快照请求使用稳定 `toolCallId` 仅用于调用关联和观测；该工具旁路 Spring Redis 工具幂等服务，不把源码快照成功结果缓存到 Redis。进程在 `quality_review` 节点执行中退出时，节点重放会重新从 Spring 读取当时最新的受限快照；节点已经完成并成功 checkpoint 时，则从有界 `repair_feedback` 继续进入 repair，修复后的下一轮审查再次从 Spring 获取最新受限快照。

本设计不调整 PostgreSQL checkpoint 表结构。业务状态表 `ai_workflow_status` 只保存节点、请求/应用标识、生成类型、质量结果和有限计数，不写入源码、完整问题、证据或 `repair_feedback`；图 checkpoint 与业务状态表是不同边界。正常成功、失败或取消终态仍 best-effort 删除对应图 thread，TTL 继续作为异常退出时的兜底清理机制。

## 14. SSE 与日志

不新增公开事件类型。前端和 Java 网关继续处理：

```text
node_status
completed
failed
```

三个 Reviewer 的内部启动、完成和具体问题不通过 SSE 暴露。

允许记录以下结构化指标：

- `review.duration_ms`
- `review.requirement.duration_ms`
- `review.function.duration_ms`
- `review.technical.duration_ms`
- `review.blocking_issue_count`
- `review.minor_issue_count`
- `review.result`
- `review.failure_code`
- `review.repair_count`
- `review.feedback_length`

禁止记录完整源码、用户完整提示词、Reviewer 原始返回正文、repair feedback 全文、密钥或模型请求头。

## 15. 文件边界

计划新增：

- `ai-service/src/ai_service/models/quality_review.py`：领域类型和严格输出解析。
- `ai-service/src/ai_service/orchestration/multi_agent_review.py`：并发协调、超时、聚合和反馈生成。
- `ai-service/tests/test_quality_review.py`：结构校验和聚合规则测试。
- `ai-service/tests/test_multi_agent_review.py`：并发、超时、取消和异常传播测试。

计划修改：

- `ai-service/src/ai_service/config.py`
- `ai-service/src/ai_service/models/base.py`
- `ai-service/src/ai_service/models/openai_compatible.py`
- `ai-service/src/ai_service/prompts/review.py`
- `ai-service/src/ai_service/orchestration/workflow.py`
- `ai-service/tests/conftest.py`
- `ai-service/tests/test_openai_compatible.py`
- `ai-service/tests/test_api.py`
- `ai-service/tests/test_gateway_and_config.py`
- `ai-service/tests/test_prompts.py`
- `ai-service/tests/test_package_structure.py`
- `ai-service/.env.example`
- `ai-service/README.md`
- `doc/ai-service-phase-one-handoff.md`

预计无需修改 Spring、前端、内部工具契约和数据库迁移文件。

## 16. 测试要求

### 16.1 聚合与校验

- 三个 Reviewer 均无问题时通过。
- 只有 minor 时通过。
- 任一 critical 或 major 时进入 repair。
- 重复问题正确归并并保留最高严重度。
- 问题数量和字段长度受到硬限制。
- 非 JSON、顶层类型错误、缺字段、未知角色、角色错配、未知严重度和超限内容均导致失败。

### 16.2 并发与取消

- 三个 Reviewer 并发启动。
- 整体耗时由最慢 Reviewer 决定，而不是三次调用串行相加。
- 任一 Reviewer 异常时取消其余任务。
- 整体超时时取消全部任务。
- 用户停止时返回 `cancelled`，不误报模型错误。
- 取消后不调用 repair，不发送 completed。

### 16.3 工作流

- 功能开关关闭时 Vue 沿用现有单 Reviewer。
- HTML 和 MULTI_FILE 始终保持现有路径。
- Vue 开启后首次生成即读取真实源码快照。
- 首次快照调用 ID 使用 `repairCount=0`。
- 每次修复后读取新快照。
- 快照源码不进入事件。
- critical/major 反馈传入 repair context。
- minor 不进入 repair feedback。
- 修复后重新构建并重新三方审查。
- 最多两次修复限制保持不变。
- Reviewer 系统故障不消耗修复次数。
- Checkpoint 状态摘要不包含源码和完整审查内容。

### 16.4 验证命令

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother/ai-service

uv run python -m compileall -q src
uv run pytest tests/test_quality_review.py -q
uv run pytest tests/test_multi_agent_review.py -q
uv run pytest tests/test_openai_compatible.py -q
uv run pytest tests/test_api.py -q
uv run pytest
uv lock --check

Set-Location D:/VibeForge/yu-ai-code-mother
git diff --check
git status --short
```

## 17. 人工验证

以下项目依赖真实模型和完整本地环境，需要实施后人工验证并同步到交接文档：

1. 本机 Docker PostgreSQL checkpoint 正常可用。
2. Spring、Python AI 服务和 Vue 前端全部启动。
3. 开启多 Agent 功能开关并生成包含明确功能要求的 Vue 项目。
4. 确认首次构建后审查真实源码快照。
5. 制造或诱导明显功能缺失，确认产生 major 或 critical 问题。
6. 确认 repair 针对具体反馈修复，并保持未提及功能。
7. 确认修复后重新构建和重新执行三个 Reviewer。
8. 审查期间停止生成，确认 Reviewer 被取消且旧预览不刷新。
9. 模拟错误 API Key、超时或非法模型返回，确认候选版本不发布。
10. 对比开启前后的耗时、token 消耗和修复成功率。

不得在未实际执行时声称 Docker、真实模型或端到端验证已经通过。

## 18. 灰度顺序

```text
功能开关默认关闭
→ Fake Model 自动测试通过
→ 本机真实模型单请求验证
→ 本机取消、超时和修复验证
→ 测试环境开启
→ 观察延迟、token 成本和修复成功率
→ 决定是否默认开启
→ 后续再评估 HTML 和 MULTI_FILE
```

第一阶段不增加百分比灰度配置。需要灰度时，通过只在指定 Python AI 服务实例开启功能实现。

## 19. 验收标准

设计的实现只有同时满足以下条件才可视为完成：

1. Vue 多 Agent 开关默认关闭，关闭时行为与当前版本兼容。
2. 开启后首次生成和每次修复均审查真实有界源码快照。
3. 三个 Reviewer 并发、只读且职责独立。
4. 所有模型输出经过严格结构和长度校验。
5. 聚合结果完全由确定性代码计算。
6. critical/major 进入 repair，minor 不消耗修复次数。
7. Reviewer 系统异常按 F1 失败，不回退、不盲修、不发布。
8. 用户取消能够终止所有 Reviewer，并保留现有 cancelled 语义。
9. 修复上限、Spring 所有权、checkpoint 边界和公开 SSE 协议保持不变。
10. 自动测试全部通过，人工验证项明确记录真实结果或未执行原因。
11. README、`.env.example` 和阶段交接文档与代码同步。
