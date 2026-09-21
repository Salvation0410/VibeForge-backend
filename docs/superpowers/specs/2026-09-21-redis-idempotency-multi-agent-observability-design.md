# Redis 工具幂等与多 Agent 演进设计

## 1. 目标与实施边界

本设计分为两个独立阶段，必须按顺序实施：

1. 将 Spring 内部 AI 工具调用幂等从进程内 Map 迁移到 Redis，为重试、多实例和后续多 Agent 提供稳定执行边界。
2. 在生产化基础稳定后，引入 CloseAI 单模型多 Agent 工作流，并使用 LangSmith 进行链路监控和评估。

第一阶段不修改生成 Agent 架构，第二阶段不改变 Spring 对业务数据、项目文件和发布终态的所有权。公共 NDJSON 事件类型和前端 SSE 协议均保持兼容。

## 2. 第一阶段：Redis 工具幂等

### 2.1 当前问题

`InternalAiToolsController` 当前使用静态 `ConcurrentHashMap<toolCallId, result>` 保存成功结果。该实现只在单个 Spring 进程内有效，服务重启后丢失，多实例之间不共享，也无法识别同一个调用 ID 被不同应用、请求或参数错误复用。

### 2.2 请求契约

内部工具请求增加请求级作用域：

```json
{
  "appId": "42",
  "requestId": "req-1",
  "toolCallId": "req-1:vue:1",
  "toolName": "file_read",
  "arguments": {
    "relativeFilePath": "src/App.vue",
    "codeGenType": "VUE_PROJECT"
  }
}
```

`appId`、`requestId` 和 `toolCallId` 由 Python 工作流生成或注入，模型不得提供。迁移期间 Spring 可以暂时从 `arguments.appId` 兼容读取应用 ID，但 Python 新请求必须发送顶层字段，兼容入口在完成灰度后删除。

### 2.3 Redis 状态

幂等 key：

```text
ai:tool:idempotency:v1:{appId}:{requestId}:{toolCallId}
```

value 保存：

```json
{
  "status": "RUNNING | SUCCEEDED",
  "toolName": "file_read",
  "requestFingerprint": "sha256...",
  "result": {},
  "startedAt": "2026-09-21T12:00:00Z",
  "completedAt": "2026-09-21T12:00:01Z"
}
```

请求指纹由标准工具名和规范化 JSON 参数计算。标准工具名来自已实现的 `InternalAiTool`，避免历史别名产生不同指纹。

### 2.4 执行流程

新增 `ToolInvocationIdempotencyService`，统一负责 Redis 状态、分布式锁、指纹比较、结果序列化和 TTL。控制器只负责鉴权、请求校验和工具分发。

```text
authenticate
  -> validate appId/requestId/toolCallId/toolName
  -> normalize tool name
  -> calculate request fingerprint
  -> acquire scoped Redisson lock
     -> no record: write RUNNING, execute once
     -> matching SUCCEEDED: return cached result
     -> different fingerprint: TOOL_IDEMPOTENCY_CONFLICT
     -> existing RUNNING: TOOL_EXECUTION_INDETERMINATE
  -> success: write SUCCEEDED with TTL
  -> explicit failure: delete RUNNING and propagate the original error
```

锁等待超时返回 `TOOL_EXECUTION_BUSY`，不得无限阻塞请求线程。成功结果和状态默认保留 24 小时，通过 Spring 环境变量配置。

### 2.5 崩溃语义

Redis 与文件系统之间没有共同事务，因此本设计不声称提供严格的 exactly-once。进程可能在文件操作成功后、Redis 写入成功结果前崩溃。此时保留的 `RUNNING` 表示执行结果不确定，系统拒绝自动重放并返回 `TOOL_EXECUTION_INDETERMINATE`，防止重复修改文件。

`artifact_publish` 继续依靠 `VersionedArtifactStore` 的 requestId 墓碑、manifest 哈希和活动指针处理安全重放。Redis 工具幂等不能替代产物发布幂等。

### 2.6 配置

```yaml
ai:
  tool-idempotency-ttl-seconds: ${AI_TOOL_IDEMPOTENCY_TTL_SECONDS:86400}
  tool-idempotency-lock-wait-millis: ${AI_TOOL_IDEMPOTENCY_LOCK_WAIT_MILLIS:30000}
```

### 2.7 测试与验收

- 相同作用域和相同指纹只执行一次并返回同一结果。
- 多个控制器实例共享 Redis 结果。
- 同一个调用 ID 使用不同工具或参数时返回 `TOOL_IDEMPOTENCY_CONFLICT`。
- 不同 appId 或 requestId 不互相污染。
- 并发请求只有一个执行者。
- 成功结果具有 TTL。
- 明确失败后允许原调用重新执行。
- 遗留 `RUNNING` 不自动重复执行写操作。
- Python 和 Java 请求契约测试保持一致。
- `artifact_publish` 的版本化安全发布测试继续通过。

## 3. 第二阶段：CloseAI 单模型多 Agent

### 3.1 设计原则

- 所有 Agent 第一版共用一个 CloseAI 模型和同一套模型参数。
- LangGraph 负责固定拓扑、预算、取消和终态；模型不能绕过校验、构建或发布。
- Agent 使用不同 system prompt、结构化输出和工具权限，不依靠无限增长的共享消息列表协作。
- Spring 仍是文件、构建和发布的唯一所有者。
- 多 Agent 通过配置灰度启用，不在发生文件写入后自动回退单 Agent。

### 3.2 工作流

```text
START
  -> input_guard
  -> context_prepare
  -> planner_agent
  -> branch_router
       HTML        -> html_coder_agent
       MULTI_FILE  -> multi_file_coder_agent
       VUE_PROJECT -> vue_coder_agent <-> Spring file tools
  -> deterministic_validation
  -> VUE_PROJECT: deterministic_build
  -> reviewer_agent
       PASS   -> publish/finalize
       REPAIR -> repair_agent -> validation
       FAIL   -> failed
  -> HTML/MULTI_FILE: deterministic_publish
  -> finalize
  -> END
```

编排器是确定性 LangGraph 图，不是拥有无限自由委派能力的模型 Agent。

### 3.3 Agent 职责

`PlannerAgent` 输出页面、组件、交互、资源、兼容要求和验收项，不调用工具、不生成最终代码。

`HtmlCoderAgent` 与 `MultiFileCoderAgent` 根据计划返回严格完整的候选产物，不操作文件工具。

`VueCoderAgent` 根据计划生成或修改 Vue 项目，只能使用已批准的 `dir_read`、`file_read`、`file_write`、`file_modify` 和 `file_delete`。

`ReviewerAgent` 读取计划、候选摘要、确定性校验结果和构建结果，输出 `PASS`、`REPAIR` 或 `FAIL` 及结构化问题列表。它只读，不能修改或发布产物。

`RepairAgent` 只处理 Reviewer、校验器和构建器报告的问题。修复后必须重新经过校验、构建和审查，最多两轮。

发布节点不是 Agent。`artifact_validate`、`project_build` 和 `artifact_publish` 永远不暴露给模型。

### 3.4 协作状态

工作流使用类型化状态传递：

```text
request metadata
requirements
implementation plan
artifact or changed files
validation result
build result
review decision and issues
repair history
tool/model/transition counters
token usage
current agent
terminal status
```

Agent 之间传递结构化摘要。checkpoint 默认保存状态、哈希、计数和错误信息，不复制完整源码和完整模型对话。

### 3.5 模型配置

参考 `D:/AI_Agent/langchain1.2_study_total` 的 CloseAI 初始化方式，将 CloseAI 作为 OpenAI 兼容服务使用。当前统一模型配置优先，CloseAI 键作为回退来源：

```text
AI_SERVICE_MODEL_API_KEY -> CLOSEAI_API_KEY
AI_SERVICE_MODEL_BASE_URL -> CLOSEAI_BASE_URL
AI_SERVICE_MODEL_NAME -> 所有 Agent 共用
```

第一版只创建一个底层模型客户端。后续如需为 Reviewer 单独使用更强模型，再扩展按角色配置，当前不预先实现。

### 3.6 工具权限

| Agent | 读取 | 写入/修改 | 删除 | 构建 | 发布 |
| --- | --- | --- | --- | --- | --- |
| Planner | 否 | 否 | 否 | 否 | 否 |
| HTML/MULTI_FILE Coder | 否 | 否 | 否 | 否 | 否 |
| Vue Coder | 是 | 是 | 是 | 否 | 否 |
| Reviewer | 受控读取或摘要 | 否 | 否 | 否 | 否 |
| Repair | 是 | 是 | 受限制 | 否 | 否 |
| 确定性节点 | 按需 | 否 | 否 | 是 | 是 |

### 3.7 预算与错误

```dotenv
AI_SERVICE_MULTI_AGENT_ENABLED=false
AI_SERVICE_MAX_MODEL_CALLS=12
AI_SERVICE_MAX_AGENT_TRANSITIONS=16
AI_SERVICE_AGENT_TIMEOUT_SECONDS=90
AI_SERVICE_MAX_REPAIR_ATTEMPTS=2
AI_SERVICE_VUE_MAX_TOOL_CALLS=4
```

模型超时、结构化输出无效、预算耗尽、取消和工具失败均转换为稳定错误码。任何写操作发生后，不自动切换回单 Agent 继续生成。

## 4. LangSmith 监控与评估

LangSmith 只负责追踪、监控和评估，不替代 Redis checkpoint，不参与发布仲裁，LangSmith 故障也不得导致生成失败。

每个生成请求建立一个根 Trace：

```text
generation/{requestId}
  planner_agent
  coder_agent
  tool_call/*
  artifact_validation
  project_build
  reviewer_agent
  repair_agent/*
  artifact_publish
  finalize
```

Trace metadata 包含 requestId、appId、userId、生成类型、引擎、工作流模式、Agent 名称、模型名称、修复次数、工具调用次数、状态和错误码。

监控指标包括 Agent 耗时、首 Token 延迟、总耗时、Token 用量、工具失败率、Reviewer 首次通过率、修复成功率、校验/构建/发布失败率、取消率、预算耗尽率以及 CloseAI 超时和限流次数。

生产环境默认隐藏 LangSmith 输入和输出，不上传完整用户提示、源码和工具参数；只记录长度、文件名、哈希、错误码和结构化摘要。测试环境仅在使用无敏感数据时允许查看完整输入输出。

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=yu-ai-code-mother
LANGSMITH_HIDE_INPUTS=true
LANGSMITH_HIDE_OUTPUTS=true
```

## 5. 灰度、回滚与阶段顺序

保留现有单工作流，通过 `AI_SERVICE_WORKFLOW_MODE=single|multi_agent` 选择。初期默认 `single`，多 Agent 仅对测试环境或指定应用开放。公共事件类型保持 `content_delta`、`tool_started`、`tool_finished`、`node_status`、`completed`、`failed`。

后续执行顺序：

1. Redis 工具幂等。
2. 真实三类型端到端与故障验证。
3. 多实例取消和灰度稳定性。
4. CloseAI 单模型多 Agent。
5. LangSmith Trace、指标和评估数据集。
6. 多 Agent 灰度验收。
7. 稳定后再评估按 Agent 选择不同模型。
