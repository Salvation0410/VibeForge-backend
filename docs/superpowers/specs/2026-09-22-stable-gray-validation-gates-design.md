# 稳定灰度与真实环境验收门设计

## 1. 背景

第一阶段已经完成 Python LangGraph 主链路、Spring 工具执行边界、HTML/MULTI_FILE 安全发布、Vue 构建失败修复状态机和 Redis 工具幂等。当前阻碍 LangGraph 提高灰度比例的主要问题不是继续扩充 AI 能力，而是灰度路由不稳定，以及真实 Spring、Python、Redis、模型和文件系统之间缺少可重复执行的验收门。

当前 `DelegatingAiGenerationGateway` 使用 `Objects.hash(userId, requestId)` 计算灰度桶。同一用户的 `requestId` 每次变化，因此可能在 Legacy 和 LangGraph 之间漂移；配置项 `graySalt` 尚未参与计算。现有测试以单元测试、MockMvc、MockTransport 和 Fake Model 为主，尚不能替代真实网络和端到端验收。

本轮修复稳定灰度并建立验收入口，但不启动真实服务、不连接真实 Redis、不调用真实模型，也不提高实际灰度比例。真实请求由用户在下一阶段执行。

## 2. 目标

1. 使用稳定业务主体和 `graySalt` 计算可重复的灰度桶。
2. 保证路由、生成和取消为同一业务主体选择相同引擎。
3. 提供只记录摘要与哈希的 Legacy/LangGraph 对比工具。
4. 提供真实 Spring/Python HTTP、Redis 幂等和三类型生成的安全验收入口。
5. 通过离线自动化测试验证新增代码和脚本，不把未执行的真实验收写成通过。

## 3. 非目标

本轮不包含：

- 将 Python `CancellationRegistry` 迁移到 Redis；
- 修改 `completed` 事件或公共 SSE 协议；
- 引入多 Agent、LangSmith 或新的模型供应商；
- 删除 Legacy LangChain4j 或 Java LangGraph4j 实验代码；
- 自动修改运行环境的 `AI_ENGINE`；
- 启动 Spring、Python、MySQL、Redis 或前端；
- 发起真实模型生成、提高灰度比例或推送远端分支。

## 4. 稳定灰度路由

### 4.1 业务主体

灰度主体按以下优先级选择：

```text
userId 存在     -> user:<userId>
userId 不存在   -> app:<appId>
appId 也不存在  -> request:<requestId>
```

正常已登录生成以用户为稳定主体，因此同一用户跨应用、跨请求保持在同一引擎。`appId` 用于缺少用户标识但已有应用上下文的内部或兼容场景；`requestId` 只作为两者均不可用时的确定性回退。

### 4.2 算法

`DelegatingAiGenerationGateway` 使用 UTF-8 编码计算：

```text
SHA-256(graySalt + ":" + subject)
  -> 取前 4 字节作为非负整数
  -> 对 100 取模
```

白名单判断优先于百分比。`grayPercentage` 在运行时限制到 0 至 100。固定 `legacy`、固定 `langgraph` 和未知配置回退 Legacy 的行为保持不变。

内部委派方法同时接收 `appId`、`userId` 和 `requestId`。`route`、`generate`、`cancel` 必须调用同一方法，避免取消被发送到不同引擎。

### 4.3 测试

新增 `DelegatingAiGenerationGatewayTest`，覆盖：

- 同一用户在不同 `requestId` 下稳定落桶；
- `graySalt` 参与计算；
- 白名单始终进入 LangGraph；
- 百分比 0、100 及越界值；
- 无用户时使用 `appId`，无应用时使用 `requestId`；
- `route`、`generate`、`cancel` 路由一致；
- 固定引擎和未知配置的兼容行为。

## 5. 双引擎摘要对比

新增 `scripts/compare-ai-generation-engines.ps1`。脚本接收两个已经按目标引擎启动的 Spring 地址，以及两个明确的测试应用 ID：

```text
LegacyBaseUrl
LangGraphBaseUrl
LegacyAppId
LangGraphAppId
```

脚本不得通过修改当前进程环境变量假装切换已经运行的 Spring 实例。登录凭据从参数或现有测试环境变量读取，不写入报告。

每次运行只记录：

- `engine`
- `appId`
- `codeGenType`
- `requestId`
- `terminalStatus`
- `toolNames`
- `artifactHashes`
- `buildStatus`
- `errorCode`
- `durationMs`

报告默认写入 Git 忽略的 `target/ai-validation/`。报告不得包含完整提示词、源码、Cookie、Bearer Token、工具参数或模型原始响应。

## 6. Spring/Python HTTP 验收入口

新增 `scripts/test-ai-service.ps1`，并增加 opt-in 的真实 Spring 网关测试。执行真实测试时必须显式提供 Spring 地址、Python 地址、内部令牌和只读测试应用 ID；Vue 构建检查还需单独提供可处置的 Vue 测试应用 ID。

真实网络部分验证：

- Spring 与 Python 健康检查；
- 正确、缺失和错误 Bearer 令牌；
- 成功工具调用及 `{code,data,message}` 解析；
- HTTP 200 中的非零 Spring 业务码；
- 缺少 `appId`、`requestId`、`toolCallId` 或工具名；
- 稳定幂等错误码保留；
- 其他业务消息、内部路径和异常信息脱敏；
- 构建调用使用独立的长读取超时。

非法 JSON、字段类型错误和缺少结果等无法由正常 Spring 进程稳定制造的协议异常，继续使用 Python MockTransport 覆盖。真实测试和模拟协议错误共同构成契约门，不把其中任意一类误称为完整端到端验证。

## 7. Redis 幂等故障窗口

扩展默认跳过的 `ToolInvocationIdempotencyRedisIT`。只有显式设置 `AI_REDIS_INTEGRATION=true` 时才连接 `AI_REDIS_URL`。

真实 Redis 验收点包括：

- 两个独立 Redisson 客户端共享成功结果；
- 相同作用域但工具名或参数指纹不同会发生冲突；
- 并发锁竞争返回稳定结果或忙状态；
- 预置陈旧 `RUNNING` 状态后返回 `TOOL_EXECUTION_INDETERMINATE`；
- action 成功但 Redis 完成状态写回失败时返回不确定状态；
- 不确定状态重试不得再次执行 action；
- Redis 不可用时明确失败，不静默降级为进程内幂等。

该测试验证故障语义，不承诺文件系统和 Redis 之间严格 exactly-once。

## 8. 三类型端到端验收脚本

新增 `scripts/test-ai-phase-two-e2e.ps1`。脚本必须显式接收 HTML、MULTI_FILE、VUE_PROJECT 三个独立测试应用 ID，并要求额外的 `-Execute` 开关。缺少任意参数时只显示检查清单并失败退出，不发送生成请求。

每类应用验证：

1. 首次生成只出现一个成功终态。
2. 二次修改保留未指定的文字、图片、功能和操作方式。
3. HTML/MULTI_FILE 只有发布成功后才切换活动版本。
4. HTML 通过严格解析、确定性校验和 Selenium 烟测。
5. MULTI_FILE 包含完整匹配的三个文件。
6. Vue 文件操作通过 Spring 工具事件，构建失败进入修复或明确失败。
7. 发布或构建失败时保留上一活动版本和旧预览。
8. 停止、网络中断和迟到回调不刷新预览、不覆盖活动版本。
9. Legacy 配置仍可作为回滚路径工作。

脚本自动判断事件顺序、终态、错误码、版本变化和构建状态。页面视觉、需求满足程度及二次修改的语义保持由脚本列为人工检查项，不伪装成完全自动判断。

## 9. 安全与错误处理

- 所有真实验收脚本默认不执行破坏性请求，必须使用显式执行开关和测试应用 ID。
- 密码优先从现有测试环境变量读取；令牌、Cookie 和密码不得出现在日志或报告中。
- 请求失败时记录阶段、HTTP 状态、业务错误码和脱敏消息，不记录响应中的完整源码。
- 健康检查、登录或必要参数失败时快速终止，不继续生成。
- 每个应用生成结果独立记录，前一类型失败不得被后一类型成功覆盖。
- 脚本不得自动修改生产配置、删除应用、覆盖事故应用或提高灰度比例。

## 10. 文档同步

同步更新：

- `doc/ai-service-startup.md`
- `doc/ai-service-phase-one-handoff.md`
- `ai-service/README.md`
- 根目录 `AGENTS.md` 中与当前 Redis 工具幂等实现不一致的描述

文档必须分别标明“离线测试已通过”“真实测试入口已提供”和“真实环境尚未执行”。如果真实 Redis、模型或端到端测试没有在本轮运行，不得写成通过。

## 11. 本轮验证

Java 至少执行：

```powershell
mvn test -Dtest=DelegatingAiGenerationGatewayTest,LangGraphAiGenerationGatewayTest,ToolInvocationIdempotencyServiceTest,InternalAiToolsHttpContractTest
mvn clean -DskipTests compile
```

Python 至少执行：

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

此外执行 PowerShell 脚本解析或参数拒绝测试、`git diff --check`，并分别检查后端和前端工作树。本轮不使用真实执行开关，不占用 8000 端口，不连接真实模型或真实 Redis。

## 12. 完成标准

- 稳定灰度算法及路由一致性测试通过；
- 双引擎报告只包含允许的摘要字段；
- HTTP、Redis 和三类型验收入口具备显式安全门；
- 脚本缺少必要参数时不会发起生成或修改项目；
- Java、Python 离线测试和编译通过；
- 文档与当前源码边界一致；
- 真实 Redis、真实模型和端到端验收保持“待执行”状态；
- 未修改或提交 `projects/`、本地 `.env`、无关前端文件及其他用户改动。
