# LangGraph 主动取消设计

## 1. 背景与问题

当前前端点击“停止生成”后会立即关闭 SSE、清理本地流式进度，并把界面状态改为“已停止”。Spring 通过下游取消回调把生成租约从 `ACTIVE` 标记为 `CANCELLED`，再通知实际 AI 引擎取消；但应用级租约只有在底层生成流真正结束后才释放。

LangGraph 新链路当前只把取消信息写入进程内 `CancellationRegistry`，工作流在节点开始、发布前、完成前和 Vue 工具调用之间检查该标记。正在等待的模型请求、Spring 工具请求或项目构建不会被取消标记立即打断。因此前端可能已经显示“已停止”，后台仍在执行，用户再次发送时会收到 `GENERATION_IN_PROGRESS`。

本次改动需要让前端展示真实停止状态，并让 LangGraph 新链路主动结束模型任务和项目构建。Legacy 链路继续保持兼容式协作取消，在自然终止前展示 `STOPPING`，不尝试重写 LangChain4j 的底层流实现。

## 2. 目标

- 点击停止后，前端先进入 `STOPPING`，不能立即显示“已停止”。
- Spring 提供经过应用权限校验的显式取消和生成状态查询接口。
- LangGraph 新链路主动取消当前 `asyncio.Task`，尽快结束正在等待的异步模型请求。
- Vue 项目构建期间停止时，Spring 主动终止本轮 npm 构建进程及其后代进程。
- 只有旧执行链真正结束并释放应用租约后，前端才恢复发送能力。
- 取消获胜后，旧任务不得校验、构建、发布或刷新预览。
- 已进入提交临界区时，迟到取消不得反转成功终态。
- 重复取消保持幂等，旧请求不能取消同一应用的新请求。

## 3. 非目标

- 不改造 Legacy 模型客户端以强制中断底层 TokenStream。
- 不实现跨 Python worker 或跨 Python 实例共享活动任务注册表；当前仍按单 worker、单实例部署。
- 不在本次引入 Vue 源码事务或取消后的文件级回滚。
- 不提前释放应用租约，也不允许旧任务未退出时启动同一应用的新生成。
- 不使用 LangGraph `interrupt()` 作为取消机制。动态中断用于可恢复暂停，不能中断正在运行的模型请求或 Spring 构建进程。

## 4. 状态模型

### 4.1 Spring 权威状态

继续使用 `GenerationLeaseService` 的 Redis 状态：

- `ACTIVE`：生成正在执行。
- `CANCELLED`：取消已经获胜，后台正在停止和清理。
- `COMMITTING`：产物提交已经开始，取消不能再获胜。
- `COMMITTED`：产物已经提交，等待生成链完成收尾。

对前端映射为：

| Redis 状态 | API 状态 | 是否允许新生成 |
| --- | --- | --- |
| 无活动状态 | `IDLE` | 是 |
| `ACTIVE` | `RUNNING` | 否 |
| `CANCELLED` | `STOPPING` | 否 |
| `COMMITTING` | `COMMITTING` | 否 |
| `COMMITTED` | `COMMITTING` | 否，等待租约释放 |

### 4.2 前端状态

前端使用明确的 UI 状态：

- `idle`
- `running`
- `stopping`

`streaming`、发送按钮、输入框和停止按钮均由该状态派生，避免本地 boolean 与后端真实状态冲突。

## 5. Spring API

### 5.1 取消当前应用生成

新增：

```http
POST /api/apps/chat/gen/code/cancel
Content-Type: application/json

{"appId": 123}
```

响应数据：

```json
{
  "appId": 123,
  "requestId": "当前活动请求 ID",
  "status": "STOPPING"
}
```

控制器先校验登录用户是应用所有者，再调用应用服务。前端不传入 `requestId`；Spring 从应用的权威 Redis 状态中读取当前活动请求，避免旧页面或伪造请求取消新任务。

取消语义：

- `ACTIVE`：原子转换为 `CANCELLED`，通知本轮选中的 AI 引擎，并请求取消同一 `requestId` 的项目构建。
- `CANCELLED`：幂等返回 `STOPPING`。
- `COMMITTING` 或 `COMMITTED`：返回 `COMMITTING`，不声称停止成功。
- 无活动状态：返回 `IDLE`。

### 5.2 查询当前生成状态

新增：

```http
GET /api/apps/chat/gen/code/status?appId=123
```

响应数据：

```json
{
  "appId": 123,
  "requestId": "当前请求 ID 或 null",
  "status": "RUNNING"
}
```

该接口同样校验应用所有权，用于停止后的前端轮询和页面重新进入时的状态同步。

## 6. Spring 租约与引擎取消

`GenerationLeaseService` 新增按 `appId` 读取状态、取消当前活动请求和返回状态快照的能力。状态转换仍通过现有 transition lock 串行化，取消接口不能直接解锁应用级生成锁。

现有 SSE 断开回调继续保留，作为浏览器刷新、关闭页面或网络断开的兜底。显式取消接口与 SSE 断开可能同时发生，二者必须幂等地指向同一个活动 `requestId`。

租约仍只在现有生成执行流的 `doFinally` 中释放。这样可以保证模型任务、工具请求和构建进程全部结束后，下一轮生成才可能获得应用锁。

## 7. Python 活动任务取消

### 7.1 活动任务注册表

新增进程内 `ActiveGenerationRegistry`：

```text
thread_id -> asyncio.Task
```

提供：

- `register(thread_id, task)`
- `cancel(thread_id)`
- `clear(thread_id)`
- `is_active(thread_id)`

工作流流式执行创建后台 task 后立即登记，在成功、失败、取消或异常收尾时移除。

### 7.2 统一主动取消入口

Python 显式取消接口和请求断线处理统一执行：

1. 在 `CancellationRegistry` 写入取消标记。
2. 在 `ActiveGenerationRegistry` 查找活动 task。
3. 对未完成 task 调用 `task.cancel()`。
4. 重复取消返回相同状态，不重复创建取消任务。

现有 `_raise_if_cancelled()` 节点和工具边界检查继续保留，作为 task cancellation 未能及时传播时的第二层保护。

### 7.3 取消终态

`GenerationWorkflow` 明确处理 `asyncio.CancelledError`：

- 不转换为普通 `GENERATION_FAILED`。
- 最多产生一个稳定的 `cancelled` 失败终态。
- 不执行后续校验、质量检查、构建或发布。
- 清理业务 checkpoint、LangGraph checkpoint、活动任务和取消标记。
- 结束 NDJSON 流，使 Java 读取协程进入终态并触发租约释放。

异步模型请求被取消后，本地 HTTP 等待应尽快终止；模型供应商是否立即停止服务端计算不作为本项目可保证的行为。

## 8. Vue 构建进程取消

Python task 取消不能自动终止 Spring 已经启动的 npm 进程，因此构建必须由 Spring 独立治理。

`InternalAiToolsController` 调用 `project_build` 时把顶层 `requestId` 传入 `VueProjectBuilder`。构建器把当前执行从单纯的 `projectPath -> CompletableFuture` 扩展为包含以下信息的受控执行记录：

- `requestId`
- 规范化项目路径
- 构建 Future
- 当前根进程
- 已捕获的后代进程
- 取消标记

Spring 取消应用生成时调用 `VueProjectBuilder.cancel(requestId)`：

1. 标记本轮构建已取消。
2. 终止根进程和已捕获的后代进程。
3. 使用现有有界终止时间等待清理。
4. 构建返回稳定错误码 `VUE_BUILD_CANCELLED`。
5. 清理活动构建映射。

取消构建不得删除上一成功 `dist`，不得把不完整构建当成成功，也不得触发预览刷新。构建进程未确认终止前，Spring 不释放生成租约。

## 9. 前端交互

### 9.1 点击停止

前端执行顺序：

1. UI 从 `running` 进入 `stopping`。
2. 停止接收和渲染迟到分片。
3. 调用 Spring 显式取消接口。
4. 取消被接受后关闭当前 EventSource。
5. 开始查询应用生成状态。

停止期间：

- 按钮显示 loading 和“正在停止…”。
- 输入框和发送按钮禁用。
- 聊天占位消息显示“正在停止本轮生成，请稍候。上一版预览将保持不变。”
- 旧预览保持挂载，不刷新。

### 9.2 确认停止完成

状态查询返回 `IDLE` 后：

- UI 进入 `idle`。
- 消息改为“已停止本轮生成，上一版预览保持不变。”
- 恢复输入框和发送按钮。
- 停止状态轮询。

查询返回 `COMMITTING` 时显示“当前结果已进入提交阶段，无法再停止，请等待完成”，并继续等待真实终态，不能把成功结果覆盖为停止。

### 9.3 轮询策略

- 前 10 秒每 500ms 查询一次。
- 10 秒后每 2 秒查询一次。
- 60 秒后保留 `stopping`，提示后台仍在清理，并允许用户手动刷新状态。
- 页面卸载、应用切换或状态进入 `IDLE` 时停止轮询。

取消请求超时或网络状态不确定时，前端先查询状态，不直接显示取消失败或成功。明确返回仍为 `RUNNING` 时允许再次点击停止。

## 10. Legacy 兼容行为

Legacy 继续调用现有 `facade.cancelGeneration(requestId)`，阻止后续构建或发布，但不保证立即终止底层 TokenStream。

前端在 Legacy 请求自然退出并释放租约前持续显示 `STOPPING`。两条引擎都遵守：

- 取消后不发布候选。
- 不刷新预览。
- 租约释放前不允许下一轮生成。
- 只有后端状态变为 `IDLE` 才显示“已停止”。

## 11. 错误与竞争处理

- 显式取消和 SSE 断开并发：只允许第一次 `ACTIVE -> CANCELLED` 转换获胜。
- 取消和提交并发：现有 transition lock 决定唯一胜者。
- 取消获胜：发布返回 `GENERATION_CANCELLED`。
- 提交获胜：取消接口返回 `COMMITTING`，成功终态不被反转。
- 旧页面重复取消：Spring 只读取当前活动请求，不接受前端指定旧 `requestId`。
- Python 活动 task 不存在但 Redis 已 `CANCELLED`：保持 `STOPPING`，等待 Java HTTP 流和工具调用结束；记录结构化告警。
- 构建终止失败：不得提前释放租约；记录进程和请求的脱敏诊断信息。

## 12. 可观测性

增加包含 `requestId`、`appId`、`userId`、`engine`、状态和耗时的结构化日志：

- 取消请求收到。
- `ACTIVE -> CANCELLED` 转换成功。
- Python task 取消请求和完成。
- 构建进程取消请求和进程树终止。
- 生成租约释放。
- 从停止请求到 `IDLE` 的总耗时。

日志不得包含完整提示词、源码、工具参数值、Bearer Token 或绝对项目路径。

## 13. 测试范围

### 13.1 前端

- 点击停止进入 `stopping`，不能立即进入 `idle`。
- `stopping` 时输入和发送被禁用。
- 查询到 `IDLE` 后恢复发送。
- 迟到分片不再修改消息。
- 停止和取消失败不刷新预览。
- `COMMITTING` 不显示停止成功。
- 页面卸载后停止轮询。
- 重复停止不创建重复轮询。

### 13.2 Spring

- 仅应用所有者可以取消和查询状态。
- `ACTIVE -> CANCELLED`、重复取消、无活动状态和提交竞争。
- 取消调用正确的引擎和当前请求 ID。
- 取消接口不提前释放租约。
- 引擎终止后释放租约并返回 `IDLE`。
- 构建取消终止父进程和后代进程。
- 构建取消返回 `VUE_BUILD_CANCELLED`。

### 13.3 Python

- 阻塞模型调用可以通过活动任务注册表取消。
- 显式取消和连接断开共用主动取消逻辑。
- 取消只产生一个终态，不产生 `completed`。
- 取消后不调用后续工具、校验、构建或发布。
- checkpoint、活动 task 和取消标记均被清理。
- 重复取消幂等。

### 13.4 真实验收

- 模型生成、工具循环、npm install、npm build 和质量检查期间分别停止。
- 停止后立即尝试再次发送，前端保持 `STOPPING`，后端不启动并发任务。
- 后台清理完成后前端自动恢复发送。
- 停止过程中刷新页面，重新进入后恢复真实状态。
- 取消候选不发布，上一版预览和上一成功 `dist` 保持不变。
- Legacy 请求显示 `STOPPING` 并在自然结束后恢复 `IDLE`。

## 14. 完成标准

- LangGraph 点击停止后立即进入真实 `STOPPING` 状态。
- 正在等待的异步模型任务能够被主动取消。
- 活动 Vue 构建的根进程和后代进程能够被有界终止。
- 后台终止前不允许同一应用开始新生成。
- 后台终止后无需刷新页面即可恢复发送。
- 取消获胜后不存在发布、预览刷新或成功 AI 历史。
- 提交获胜后不存在被迟到取消反转的成功终态。
- Legacy 保持兼容安全语义，不声称具备主动模型中断能力。
