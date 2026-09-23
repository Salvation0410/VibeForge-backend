# Python Redis 共享取消设计

## 1. 背景

Python AI 服务当前使用进程内 `CancellationRegistry` 保存协作式取消标记。单进程部署时，取消接口和工作流共享同一字典；多 worker 或多实例部署时，取消请求可能由实例 A 接收，而生成工作流运行在实例 B，B 无法观察 A 的取消标记。

Spring 已通过 `GenerationLeaseService` 的 Redis 状态门仲裁取消与提交，仍是产物能否提交的最终安全边界。本轮把 Python 取消状态迁移为 Redis 共享状态，用于跨实例尽快停止模型、工具和工作流计算，不替代 Spring 的提交仲裁。

## 2. 目标

1. 让所有 Python worker 和实例共享取消标记。
2. Redis 不可用时失败关闭，不启动或接受无法可靠取消的新生成。
3. 保持现有协作式取消和外部 SSE 终态语义。
4. 缩短节点、模型和工具边界上的取消检测延迟。
5. 保持 checkpoint 可选降级与取消强依赖两种语义相互独立。

## 3. 非目标

本轮不包含：

- 强制中断已经发出的模型 HTTP 请求；
- 强制终止 Spring 中正在运行的 npm 子进程；
- 修改 Java `GenerationLeaseService` 状态机；
- 修改公共 SSE 协议或前端代码；
- 支持断线重连、后台恢复生成或复用旧 requestId；
- 缩减 `completed` 事件；
- 引入多 Agent、LangSmith 或新的模型供应商；
- 执行真实 Redis、真实模型或三类型端到端验收。

## 4. 架构

采用独立 `RedisCancellationStore`，复用 `AI_SERVICE_REDIS_URL`，使用独立 Redis 客户端、生命周期、健康状态和 keyspace。取消实现不嵌入 `RedisCheckpoint`，避免 checkpoint 的可选降级语义影响取消的失败关闭语义。

```text
Spring cancel
  -> Python POST /internal/v1/generations/{requestId}:cancel
  -> RedisCancellationStore.cancel(threadId)
  -> SET yu-ai:cancellation:v1:<encoded-thread-id> 1 EX <ttl>

任意 Python worker
  -> await cancellation_store.is_cancelled(threadId)
  -> Redis EXISTS
  -> GenerationCancelled
  -> failed/cancelled 终态
  -> 尽力 clear(threadId)
```

生产环境始终创建 Redis 取消存储。`AI_SERVICE_REDIS_ENABLED=false` 只关闭 checkpoint，不关闭取消存储。测试可以通过应用工厂显式注入内存实现。

## 5. 组件边界

### 5.1 编排协议

`ai_service/orchestration/cancellation.py` 定义：

```python
class CancellationStore(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def cancel(self, thread_id: str) -> None: ...
    async def is_cancelled(self, thread_id: str) -> bool: ...
    async def clear(self, thread_id: str) -> None: ...
    async def ping(self) -> bool: ...
```

同一模块保留：

- `MemoryCancellationStore`：仅供测试显式注入；
- `GenerationCancelled`：内部协作式中断；
- `CancellationBackendUnavailable`：稳定错误码为 `CANCELLATION_BACKEND_UNAVAILABLE`。

### 5.2 Redis 实现

新增 `ai_service/infrastructure/cancellation.py`：

- 使用 `redis.asyncio.Redis.from_url(..., decode_responses=True)`；
- key 前缀为 `yu-ai:cancellation:v1:`；
- thread ID 使用 URL-safe Base64 编码，避免分隔符碰撞；
- `cancel()` 使用 `SETEX`；
- `is_cancelled()` 使用 `EXISTS`；
- `clear()` 使用 `DELETE`；
- `start()` 必须成功 `PING`，失败直接抛出；
- `ping()` 运行期失败返回 `False`；
- 取消写入和读取失败转换为 `CancellationBackendUnavailable`；
- TTL 复用 `checkpoint_ttl_seconds`。

### 5.3 应用装配

`create_app()` 新增可选的 `cancellations: CancellationStore | None` 参数。未注入时创建 `RedisCancellationStore(config.redis_url, ttl_seconds=config.checkpoint_ttl_seconds)`。

应用生命周期按顺序启动 checkpoint 和取消存储。取消存储启动失败必须使应用启动失败，不受 `redis_required` 和 `redis_enabled` 影响。关闭时分别释放两个客户端。

应用状态保存 `app.state.cancellations`，路由与工作流继续引用同一个存储实例。

## 6. API 与健康语义

### 6.1 新生成请求

在返回 `StreamingResponse` 前调用取消存储 `ping()`。失败时不创建工作流任务，返回 HTTP 503：

```json
{
  "detail": {
    "code": "CANCELLATION_BACKEND_UNAVAILABLE",
    "message": "Cancellation service is unavailable"
  }
}
```

### 6.2 取消请求

取消接口必须 `await cancellation_store.cancel(thread_id)` 成功后才返回 202。写入失败返回同一 HTTP 503，不得返回虚假的 `cancelled`。

### 6.3 健康检查

- `/health/live` 仍只报告进程存活；
- `/health/ready` 同时检查 checkpoint 和取消存储；
- 取消存储不健康时始终返回 503；
- checkpoint 继续遵循现有可选或必需配置，但响应增加 `cancellation` 布尔字段。

## 7. 工作流与取消检查

所有取消操作改为异步。工作流在以下边界调用 `await _raise_if_cancelled(thread_id)`：

- 每个 LangGraph 节点开始前；
- 模型生成、审查和修复返回后；
- 每个 Vue 工具调用前后；
- `artifact_validate`、`project_build` 和 `artifact_publish` 前后；
- `finalize` 前。

读取 Redis 失败不能按未取消处理。`CancellationBackendUnavailable` 在产物尚未提交时产生唯一 `failed` 终态，错误码保持 `CANCELLATION_BACKEND_UNAVAILABLE`。

取消仍是协作式的。模型或工具调用正在执行时，必须等调用返回到下一个检查点才能停止。

## 8. 竞态与终态

### 8.1 取消先于发布

进入 `artifact_publish` 前检查共享取消标记。标记存在时抛出 `GenerationCancelled`，不得调用 Spring 发布工具。

### 8.2 发布先于迟到取消

Spring 已进入 `COMMITTING` 或 `COMMITTED` 时，继续由 Java 状态门决定成功终态。Python 不得因为迟到取消或外围 Redis/checkpoint 错误反转已经提交的版本。

### 8.3 清理

工作流进入明确终态后执行 `await clear(thread_id)`。清理失败只记录告警，由 TTL 回收，不覆盖已经确定的 `completed` 或 `failed` 终态。

客户端断连时，当前实例仍会取消本地异步任务，并尽力向 Redis 写入共享标记。写入失败记录错误；Spring 已经持有的取消状态门继续禁止发布。

## 9. Java 边界

本轮不修改 Java 取消仲裁：

- Spring 先将生成租约状态转换为 `CANCELLED`；
- Java 异步通知 Python，非 2xx 记录告警；
- Python Redis 故障不能绕过 Spring 的提交门；
- Python 共享取消减少无效计算，Spring 仍是发布安全的最终裁决者。

## 10. 测试

### 10.1 离线测试

- 内存存储的取消、读取、清理和测试 TTL；
- Redis 命令、TTL、key 编码和异常转换；
- Redis 启动失败阻止应用启动；
- 生成前健康检查失败返回 503，模型未调用；
- 取消写入失败返回 503；
- 两个应用实例共享同一测试后端时跨实例可见；
- 节点、模型和工具调用前后检测取消；
- Redis 读取失败不得继续校验、构建或发布；
- 清理失败不覆盖明确终态；
- 取消先于发布时失败，发布已提交时保持成功；
- HTML、MULTI_FILE、VUE_PROJECT、修复上限和工具预算不回归。

### 10.2 真实 Redis 入口

新增默认跳过的测试，只有设置以下变量才连接真实 Redis：

```text
AI_REDIS_CANCELLATION_INTEGRATION=true
AI_REDIS_URL=redis://127.0.0.1:6379/2
```

真实测试使用两个独立 Redis 客户端验证跨实例可见性、TTL 和清理。本轮不实际执行该测试。

## 11. 文档与完成标准

同步更新：

- `ai-service/README.md`
- `doc/ai-service-startup.md`
- `doc/ai-service-phase-one-handoff.md`
- 根目录 `AGENTS.md`

完成标准：

- 生产应用不再创建进程内取消注册表；
- Redis 取消存储启动失败会阻止服务启动；
- 运行期读写失败不会被当作未取消；
- 多实例共享取消的离线契约测试通过；
- Python `compileall`、完整 `pytest` 和 `uv lock --check` 通过；
- Java相关取消与网关测试保持通过；
- 真实 Redis、真实模型和端到端验收明确保持待执行。
