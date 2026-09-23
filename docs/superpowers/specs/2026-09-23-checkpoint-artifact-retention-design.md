# LangGraph Checkpoint 产物留存收敛设计

## 背景与目标

Python 工作流当前有两类 checkpoint：

- `CheckpointStore.save()` 保存供排障和观测使用的业务状态摘要。
- `RedisGraphSaver` 保存 LangGraph 自动 checkpoint 和 pending writes，用于执行期间的节点恢复。

业务摘要仍包含完整 `artifact`，LangGraph 自动 checkpoint 也会序列化完整运行状态。这样会让生成源码在请求结束后继续留存在 Redis，增加内存占用、序列化开销和源码暴露面。

本次优化的目标是：

1. 业务 checkpoint 永久不保存完整源码。
2. LangGraph 执行期间继续保留完整状态，确保校验、修复、构建、发布和节点恢复不受影响。
3. 请求成功、失败、取消或协程被取消后，立即清理对应 thread 的 LangGraph 自动 checkpoint。
4. 清理失败不得改变已经确定的业务终态。

## 范围

本次只修改 Python AI 服务的 checkpoint 生命周期、相关单元测试和说明文档，不修改 Java 网关、公共 SSE 协议、前端行为、生成类型或 Spring 文件所有权边界。

真实 Redis、真实模型和 Spring/Python 端到端验证不在本次自动验证范围内，保留为后续真实环境验收项。

## 方案选择

采用“执行期保留，终态清理”的方案：

- `_checkpoint_payload()` 删除 `artifact`，业务摘要继续按现有 TTL 保留节点、请求、生成类型、质量和计数信息。
- `CheckpointStore` 增加清理当前 thread 的 LangGraph 数据能力。
- `RedisCheckpoint` 复用现有 `RedisGraphSaver.adelete_thread()`，删除 checkpoint、latest 指针和 pending writes。
- `DisabledCheckpoint` 和测试替身提供无操作或可观测实现，保持禁用 Redis 和单元测试场景兼容。
- `GenerationWorkflow._execute()` 在 `finally` 中执行清理，因此正常成功、业务失败、显式取消和流消费者中止均覆盖。

不采用以下方案：

- 不在 saver 写入时删除 `artifact`。运行中间节点恢复后仍需要产物完成校验、修复和发布，提前脱敏会破坏恢复语义。
- 不停用 LangGraph saver。该方案会直接移除节点恢复能力，超出本次优化范围。

## 组件与数据流

### 业务 checkpoint

`GenerationWorkflow._checkpoint_payload()` 保留以下字段：

- `node`
- `requestId`
- `appId`
- `codeGenType`
- `qualityPassed`
- `repairCount`
- `toolCallCount`

不得写入 `artifact`、完整对话、提示词、文件内容或 `currentArtifact` 等源码载荷。

### LangGraph 自动 checkpoint

工作流执行期间，`RedisGraphSaver` 维持现有序列化和 TTL 行为。每个请求继续使用唯一的 `thread_id = app_id + ":" + request_id`，因此清理只影响当前请求。

进入终态后，工作流通过 `CheckpointStore` 调用现有 `RedisGraphSaver.adelete_thread(thread_id)`。删除范围包括该 thread 下所有 namespace 和 checkpoint id 对应的：

- `yu-ai:langgraph:checkpoint:*`
- `yu-ai:langgraph:latest:*`
- `yu-ai:langgraph:writes:*`

业务摘要键不属于上述带 namespace 的自动 checkpoint 键，继续由 TTL 回收。

## 终态与异常处理

清理在工作流最外层 `finally` 执行，顺序为：

1. 图执行完成或抛出异常。
2. 工作流发送原本应有的 `completed` 或 `failed` 终态事件。
3. 清理当前 thread 的 LangGraph 自动 checkpoint。
4. 清除内存中的取消标记。

清理是终态后的维护动作，不参与业务结果判定。无论 checkpoint 配置为 required 还是 optional，清理失败均只记录包含 `thread_id` 的告警，不重新抛出，不补发第二个终态，也不把 `completed` 反转为 `failed`。业务摘要写入和 LangGraph 执行阶段原有的 required/optional 语义保持不变。

## 兼容性

- `WorkflowState.artifact` 继续存在，生成、校验、质量检查、修复和发布节点无需改变。
- 内部 NDJSON 事件和 Java SSE 适配无需改变。
- Redis 不可用时保持现有降级行为，清理操作为空操作。
- 当前没有公开的“从已结束请求继续执行”入口，因此终态清理不会移除对外承诺的恢复能力。

## 测试设计

自动测试覆盖：

1. `_checkpoint_payload()` 不包含 `artifact`，且仍保留排障所需摘要。
2. 成功、失败和取消请求均触发一次 thread 清理。
3. 协程取消仍通过 `finally` 触发清理。
4. 清理失败不会反转 `completed`，也不会覆盖原始失败或取消终态。
5. `RedisGraphSaver.adelete_thread()` 删除当前 thread 的 checkpoint、latest 和 pending writes，不删除其他 thread。
6. 执行中的校验、修复和发布节点仍可读取完整 `artifact`。
7. Redis 禁用或不可用场景保持可运行。

本轮执行 Python `compileall`、完整 `pytest`、`uv lock --check` 和 `git diff --check`。真实 Redis 的键清理、真实模型生成及 Spring/Python 端到端请求由后续真实环境验证完成。

## 文档与交付

实现完成后更新 `ai-service/README.md` 和 `doc/ai-service-phase-one-handoff.md`，明确区分：

- 业务 checkpoint 只保存无源码摘要。
- LangGraph 自动 checkpoint 仅在执行期间可能包含完整产物。
- 任一终态后会立即尝试清理，TTL 仍作为异常情况下的兜底回收机制。
