# PostgreSQL Checkpoint 重构设计

日期：2026-09-29

## 1. 背景

Python AI 服务当前使用自定义 `RedisGraphSaver` 保存 LangGraph checkpoint，并由
`RedisCheckpoint` 额外保存每个节点的精简业务状态。完整图 checkpoint 在请求执行期间可能
包含产物内容，成功、失败或取消进入终态后会按 `thread_id` 清理；精简业务状态按 TTL 短期
保留，用于验收和排障。

本轮将 checkpoint 存储迁移到 PostgreSQL。PostgreSQL 由本机 Docker 提供，但数据库本身由
Python AI 服务独占，数据库名为 `yu_ai_checkpoint`。Spring 业务数据继续保存在 MySQL，Python
不得通过该 PostgreSQL 保存用户、应用、聊天历史或项目文件。

用户已明确选择本轮不启用长期记忆。设计只接入官方 PostgreSQL checkpointer，不创建或注入
`PostgresStore`。

## 2. 目标

- 使用官方 `AsyncPostgresSaver` 替换自定义 `RedisGraphSaver`。
- 维持执行期 checkpoint 恢复、终态立即清理和异常退出 TTL 兜底语义。
- 使用同一 PostgreSQL 连接池保存精简业务状态，不保存完整源码或模型原始内容。
- 保持 `CheckpointStore` 对工作流的抽象边界，避免将数据库细节写入工作流节点。
- 保持 optional、required、disabled 三种运行模式及 `/health/ready` 语义。
- 提供独立数据库初始化入口，生产运行账号可以不长期持有 DDL 权限。
- 提供默认跳过的真实 PostgreSQL 集成测试，连接用户现有的本机 Docker PostgreSQL。
- 删除 Python AI 服务对 Redis checkpoint 的依赖、配置和文档描述。

## 3. 非目标

- 不引入 `AsyncPostgresStore`、向量检索或跨 thread 长期记忆。
- 不把 Spring 业务 MySQL 数据迁入 PostgreSQL。
- 不把 Spring 工具幂等 Redis 改为 PostgreSQL。
- 不修改 `VersionedArtifactStore` 的不可变发布机制。
- 不在仓库中保存 PostgreSQL 真实账号、密码、容器配置或数据卷路径。
- 不新增或接管用户当前运行的 PostgreSQL Docker 容器。
- 不在本轮引入 Alembic、pgvector、pg_cron 或数据库高可用方案。

## 4. 方案选择

采用“官方 Checkpointer + 独立精简业务状态表”方案：

1. `AsyncPostgresSaver` 负责 LangGraph checkpoint、blob 和 pending writes。
2. `ai_workflow_status` 负责当前 `_checkpoint_payload()` 产生的脱敏节点摘要。
3. 两者共用一个 `AsyncConnectionPool`，但生命周期和清理语义独立。

不采用只保留官方 checkpoint 的方案，因为终态清理后会失去当前已有的短期排障摘要。不自行实现
PostgreSQL saver，因为这会重复维护 LangGraph checkpoint 协议、序列化、版本链和迁移逻辑。

## 5. 依赖与兼容性

当前锁文件使用 `langgraph-checkpoint==3.0.1`。新增依赖固定在兼容版本线：

```toml
langgraph-checkpoint-postgres>=3.0.1,<3.1
psycopg[binary]>=3.2,<4
psycopg-pool>=3.2,<4
```

由 `uv` 重新解析并更新 `uv.lock`。不得直接升级到要求 `langgraph-checkpoint>=4.1` 的新主版本，
除非单独完成 LangGraph 依赖升级和回归验证。

官方 checkpointer 首次使用必须执行 `setup()`。连接池连接参数必须包含：

```text
autocommit=true
prepare_threshold=0
row_factory=dict_row
```

启用 `LANGGRAPH_STRICT_MSGPACK=true`，限制 checkpoint 反序列化类型。若现有工作流状态无法通过严格
序列化，应缩减状态类型或配置最小允许模块列表，不得直接关闭安全限制作为长期方案。

## 6. 配置

删除以下 Python checkpoint 配置：

```text
AI_SERVICE_REDIS_ENABLED
AI_SERVICE_REDIS_REQUIRED
AI_SERVICE_REDIS_URL
```

新增：

```text
AI_SERVICE_CHECKPOINT_ENABLED=true
AI_SERVICE_CHECKPOINT_REQUIRED=false
AI_SERVICE_CHECKPOINT_POSTGRES_URL=postgresql://<user>:<password>@localhost:5432/yu_ai_checkpoint
AI_SERVICE_CHECKPOINT_AUTO_SETUP=true
AI_SERVICE_CHECKPOINT_TTL_SECONDS=86400
AI_SERVICE_CHECKPOINT_POOL_MIN_SIZE=1
AI_SERVICE_CHECKPOINT_POOL_MAX_SIZE=5
LANGGRAPH_STRICT_MSGPACK=true
```

规则：

- URL 仅存在于 `.env` 或运行时密钥配置，`.env.example` 使用占位值。
- `POOL_MIN_SIZE` 最小为 1，`POOL_MAX_SIZE` 不小于最小值并设置合理上限。
- `CHECKPOINT_TTL_SECONDS` 同时控制精简业务状态和异常退出图 checkpoint 的兜底清理时间。
- `AUTO_SETUP=true` 适合本地开发；生产推荐先运行独立初始化命令，再以 `false` 启动服务。
- 不兼容旧 `AI_SERVICE_REDIS_*` 配置。启动说明必须明确这是一次有意的配置迁移。

## 7. 组件设计

### 7.1 `CheckpointStore` 协议

保留现有方法：

```python
start()
close()
save(thread_id, state)
cleanup_graph(thread_id)
ping()
get_graph_saver()
```

`GenerationWorkflow` 不感知 PostgreSQL。测试继续通过 `MemoryCheckpoint` 或
`DisabledCheckpoint` 注入。

### 7.2 `PostgresCheckpoint`

新实现负责：

- 构造但不立即打开 `AsyncConnectionPool`。
- `start()` 打开连接池、等待可用、按配置执行 setup 和过期清理。
- 创建 `AsyncPostgresSaver(pool)` 并通过 `get_graph_saver()` 提供给 LangGraph。
- `save()` 将精简业务摘要 upsert 到 `ai_workflow_status`。
- `cleanup_graph()` 调用 `AsyncPostgresSaver.adelete_thread(thread_id)`。
- `ping()` 执行轻量 `SELECT 1` 并更新 `available`。
- `close()` 停止清理任务并关闭连接池。

`RedisGraphSaver` 和 `RedisCheckpoint` 在迁移完成后删除，不保留双后端运行分支。

### 7.3 官方 checkpoint 表

表结构和版本迁移完全交给 `AsyncPostgresSaver.setup()`。业务代码不得直接读取或修改：

- `checkpoint_migrations`
- `checkpoints`
- `checkpoint_blobs`
- `checkpoint_writes`

终态清理只调用官方 `adelete_thread()`，不复制官方删除 SQL。

### 7.4 精简业务状态表

```sql
CREATE TABLE IF NOT EXISTS ai_workflow_status (
    thread_id          text PRIMARY KEY,
    app_id             bigint NOT NULL,
    request_id         text NOT NULL,
    code_gen_type      text NOT NULL,
    node               text NOT NULL,
    quality_passed     boolean,
    repair_count       integer NOT NULL DEFAULT 0,
    tool_call_count    integer NOT NULL DEFAULT 0,
    status_payload     jsonb NOT NULL,
    updated_at         timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_workflow_status_expires_at
    ON ai_workflow_status (expires_at);
```

`status_payload` 只能来自 `_checkpoint_payload()`，允许字段为：

- `node`
- `requestId`
- `appId`
- `codeGenType`
- `qualityPassed`
- `repairCount`
- `toolCallCount`

禁止保存：

- artifact 或 Vue 源码快照
- 提示词和模型原始输入输出
- 工具参数和工具响应正文
- 账号、Cookie、令牌和绝对项目路径

### 7.5 过期清理

完整图 checkpoint 正常在终态立即删除。异常退出时依赖业务状态的 `expires_at` 作为兜底：

1. 查询并删除过期的 `ai_workflow_status`，返回对应 `thread_id`。
2. 对每个返回的 thread 调用 `adelete_thread()`。
3. 启动时执行一次。
4. 运行期由单进程内的有界定时任务周期执行，周期不超过 15 分钟且不短于 60 秒。
5. 清理单个 thread 失败不阻止其他 thread，整体失败按 required/optional 规则更新就绪状态。

不依赖 pg_cron。多实例同时清理必须是幂等的，重复删除同一 thread 不得报业务错误。

## 8. 初始化与本地 Docker

新增模块化命令，例如：

```powershell
cd ai-service
uv run python -m ai_service.infrastructure.checkpoint_setup
```

该命令：

1. 读取 `AI_SERVICE_CHECKPOINT_POSTGRES_URL`。
2. 连接 `yu_ai_checkpoint`。
3. 执行官方 `setup()`。
4. 创建精简业务状态表和索引。
5. 执行一次过期清理。
6. 不启动 FastAPI、模型客户端或 Spring 工具网关。

文档提供数据库和最小权限用户的示例 SQL，但不添加新的 Docker Compose，也不假定用户容器名、密码、
端口映射或数据卷路径。默认连接 `localhost:5432`，实际地址通过环境变量覆盖。

## 9. 故障与就绪语义

### required 模式

- 启动连接、setup 或首次探测失败：服务启动失败。
- 运行期图 checkpoint 或业务摘要写入失败：当前工作流失败。
- `/health/ready` 只有 PostgreSQL 实时可用时返回 ready。

### optional 模式

- 启动失败：记录脱敏警告，以无恢复模式运行。
- `get_graph_saver()` 返回 `None`，新请求不使用 LangGraph checkpoint。
- `save()` 成为无操作。
- `/health/ready` 返回 503，明确提示 checkpoint 未就绪。
- 不在进程内自动从无 saver 热切换到有 saver；数据库恢复后通过重启服务恢复 checkpoint。

### 终态保护

- Spring 已成功发布产物后，checkpoint 写入或清理失败不能反转成功终态。
- 清理失败只降低 checkpoint 就绪状态并记录稳定、脱敏的日志。
- 不因 PostgreSQL 故障自动切换到 Legacy；引擎回滚仍由 Spring 配置控制。

## 10. 测试与验收

### 单元测试

- 配置默认值、边界和 URL 脱敏。
- disabled、optional、required 生命周期。
- 连接池打开、关闭和 `SELECT 1`。
- `AUTO_SETUP` 开关。
- 精简摘要字段白名单和 upsert。
- 终态调用 `adelete_thread()`。
- 过期状态返回 thread 并清理图 checkpoint。
- 清理失败不反转业务终态。
- 发布成功后 checkpoint 失败仍只产生一次 completed。

### 真实 PostgreSQL opt-in 集成测试

仅当 `AI_SERVICE_POSTGRES_INTEGRATION=true` 时连接用户当前 Docker PostgreSQL，默认跳过。覆盖：

- `setup()` 首次和重复执行。
- checkpoint 保存、读取、列举和删除。
- pending writes。
- 两个独立连接的并发访问。
- 精简摘要 upsert。
- `expires_at` 清理和图 checkpoint 级联清理。
- 连接中断后的 ready 降级。

每次测试使用随机 `thread_id`，完成后删除自身创建的数据，不删除其他数据库或 schema。

### 常规验证

```powershell
cd ai-service
uv sync --frozen --python 3.12
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

更新统一验收入口，使 Python checkpoint 改动纳入自动化门禁；未设置 PostgreSQL 集成开关时不得把真实
数据库测试声明为已通过。

## 11. 长期记忆决策

本轮不创建 `PostgresStore`，原因：

- Spring 已把聊天历史作为请求上下文传给 Python。
- 当前没有明确的跨 thread 用户偏好或应用知识写入需求。
- 尚未定义用户级/应用级 namespace、写入批准、冲突覆盖、过期和删除规则。
- 长期记忆可能把过期或错误事实重新注入生成，影响已有应用修改安全。
- 长期记忆涉及用户数据导出、删除和隐私边界，不能作为 checkpoint 迁移的附带功能上线。

只有满足以下至少一项业务需求时重新评估：

- 用户明确要求跨应用保存稳定的生成偏好。
- 应用需要跨 requestId 保存结构化架构决策，而聊天历史无法可靠承载。
- 已定义可查看、修改、删除和过期的记忆产品能力。

重新评估时使用独立 `PostgresStore` namespace，不读取 checkpoint 表模拟长期记忆，也不将完整源码作为
长期记忆保存。

## 12. 文档与迁移影响

必须同步更新：

- `AGENTS.md`
- `ai-service/.env.example`
- `ai-service/README.md`
- `doc/ai-service-startup.md`
- `doc/ai-service-langchain-langgraph-refactor-design.md`
- `doc/ai-service-phase-one-handoff.md`

文档必须明确：

- Spring 工具幂等仍使用 Redis database 1。
- 迁移完成后，Python checkpoint 已改为独立 PostgreSQL，不再使用 Redis database 2。
- PostgreSQL 不属于 Spring 业务数据源。
- 长期记忆没有启用。
- 本机 Docker 只是 PostgreSQL 的运行方式，仓库不管理用户现有容器。

## 13. 回滚

代码迁移不保留 Redis checkpoint 双写。回滚方式为：

1. 切回迁移前 AI 服务提交。
2. 恢复旧 `AI_SERVICE_REDIS_*` 配置。
3. 重启 Python AI 服务。

checkpoint 只服务运行中请求，终态本应清理，因此不迁移 Redis 历史 key 到 PostgreSQL，也不尝试把
PostgreSQL checkpoint 反向转换为 Redis。执行回滚前应停止接收新 LangGraph 请求，等待或取消当前活跃
请求，避免同一 request 在两个后端产生并行 checkpoint。

## 14. 参考

- LangGraph Checkpoint Postgres README：
  `https://github.com/langchain-ai/langgraph/tree/main/libs/checkpoint-postgres`
- `AsyncPostgresSaver` 源码：
  `https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/langgraph/checkpoint/postgres/aio.py`
