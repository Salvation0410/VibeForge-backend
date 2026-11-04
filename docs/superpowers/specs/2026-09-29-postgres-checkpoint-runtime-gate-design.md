# PostgreSQL Checkpoint Runtime Gate Design

## 1. 目标

本轮关闭 Python AI 服务 PostgreSQL checkpoint 的运行门：修复单连接池清理风险，补齐官方 schema、生命周期和真实数据库集成证据，并提供默认 dry-run、显式 opt-in 的脱敏验收入口。

完成后可以证明：

- `AsyncPostgresSaver` 在连接池 `max_size=1` 时不会因过期清理嵌套取连接而阻塞。
- 独立数据库首次初始化和重复初始化幂等。
- `auto_setup=false` 只在完整 schema 已存在时启动。
- enabled/disabled、optional/required 的就绪和失败语义与文档一致。
- 正常终态、TTL 过期和图删除失败重试不会误删状态或反转业务终态。
- 真实数据库报告不包含 DSN、密码、checkpoint 内容、提示词或源码。

## 2. 当前事实

- Python checkpoint 已从 Redis 迁移到独立 PostgreSQL 数据库 `yu_ai_checkpoint`。
- 官方图 checkpoint 由 `AsyncPostgresSaver` 管理；`ai_workflow_status` 只保存脱敏节点摘要。
- 正常终态调用 `adelete_thread(thread_id)`；状态摘要由 TTL 清理兜底。
- 真实 PostgreSQL 测试为 opt-in，当前提交基线默认跳过，尚无本机独立数据库通过证据。
- 长期记忆保持关闭，不创建 `PostgresStore`，不把 checkpoint 表当作跨请求知识库。

## 3. 发现的运行风险

当前 `cleanup_expired()` 在 `async with self._pool.connection()` 内遍历过期 thread，并调用 `self._graph_saver.adelete_thread(thread_id)`。官方 saver 会从同一个池再次获取连接。

当 `pool_max_size=1` 时，调用链可能变成：

```text
cleanup_expired
  -> 持有唯一连接查询过期 thread
  -> AsyncPostgresSaver.adelete_thread
     -> 等待同一连接池的另一条连接
  -> 阻塞或超时
```

本轮必须将清理拆成三个阶段：

```text
1. 获取连接并读取过期 thread_id 列表
2. 释放连接
3. 对每个 thread：
   3.1 调用官方 adelete_thread
   3.2 仅在图删除成功后重新获取连接删除状态行
```

图删除失败时保留 `ai_workflow_status`，下一轮清理继续重试。一个 thread 失败不得阻止其他 thread 清理。

## 4. Schema 完整性

`auto_setup=false` 必须验证以下表全部存在：

```text
checkpoint_migrations
checkpoints
checkpoint_blobs
checkpoint_writes
ai_workflow_status
```

`checkpoint_migrations` 是官方 saver 管理 schema 版本的组成部分，不能只验证数据表。缺少任一表时：

- required 模式：启动失败并抛稳定错误，不泄露 URL。
- optional 模式：服务可继续运行，但 checkpoint unavailable，ready 返回 503。
- disabled 模式：不创建连接池、不连接 PostgreSQL，ready 返回 200，明确表示服务接受无恢复能力运行。

## 5. 生命周期语义

### 5.1 available

`available=True` 只表示连接池已打开、schema 完整、启动清理成功且当前 saver 可用。

运行期 `save()`、`ping()`、后台清理或终态图删除出现基础设施异常时，实例降级为 unavailable。终态清理失败不得覆盖已经确定的 `completed`、`failed` 或 `cancelled` 业务结果。

### 5.2 required 与 optional

| 模式 | 启动数据库不可用 | 运行期摘要写失败 | ready |
| --- | --- | --- | --- |
| required | 启动失败 | 抛稳定 `RuntimeError` | 不进入正常服务态 |
| optional | 降级启动 | 记录脱敏告警并降级 | 503 |
| disabled | 不连接数据库 | no-op | 200 |

### 5.3 auto setup

- 本地：允许 `AI_SERVICE_CHECKPOINT_AUTO_SETUP=true`。
- 生产：先运行 `python -m ai_service.infrastructure.checkpoint_setup`，再以 `AUTO_SETUP=false` 启动；运行账号应移除不必要的 DDL 权限。

## 6. 测试分层

### 6.1 离线单元测试

不连接数据库，验证：

- 过期查询阶段释放连接后才调用 saver。
- 成功图删除后使用新连接删除状态行。
- 单 thread 图删除失败保留状态，其他 thread 继续。
- schema 缺 `checkpoint_migrations` 时 optional/required 行为。
- `ping()` 成功和失败降级。
- `save()` optional/required 写失败。
- `cleanup_graph()` 成功委派与失败不反转终态。
- `close()` 取消后台清理任务并关闭池。
- saver 使用显式 serializer，pickle fallback 保持关闭，不增加自定义 JSON constructor 白名单。

### 6.2 真实 PostgreSQL opt-in 测试

使用随机 UUID thread，只删除本测试创建的数据，不 drop 数据库或 schema。覆盖：

- setup 首次和重复执行。
- `auto_setup=false` 使用已初始化 schema。
- `pool_min_size=1`、`pool_max_size=1` 的 TTL 清理在有界超时内完成。
- 状态摘要二次 upsert 可从独立连接看到最新值。
- `cleanup_graph()` 删除官方图 checkpoint，但状态摘要保留到 TTL。
- 强制过期后图和状态均清理。
- 两个独立连接可读写，不依赖同一连接对象。

真实故障注入（停止容器、断网、kill backend）不由默认自动化执行。只有用户确认 disposable 环境后，才进行 required/optional down 场景。

## 7. 专项验收脚本

新增 `scripts/verify-postgres-checkpoint-runtime.ps1`：

- 默认 dry-run，不连接 PostgreSQL、不写报告。
- `-Execute` 必须要求当前进程已设置 `AI_SERVICE_CHECKPOINT_POSTGRES_URL`。
- 不接受明文 URL 命令行参数，不输出环境变量值。
- 依次执行 setup、真实 PostgreSQL 集成测试、离线重点测试、compileall 和 lock check。
- 报告写入 ignored 的 `target/ai-validation/postgres-checkpoint-runtime.json`。
- 报告只记录 commit、步骤名、命令标签、状态、退出码、耗时和人工待验收项。
- required/optional down 故障注入只写入人工清单，不自动停止用户容器。

新增独立静态/行为测试脚本，验证 dry-run、不泄密、失败退出码和报告字段。中央统一门禁只在专项脚本稳定后串行接入，避免并行 Agent 同时修改共享脚本。

## 8. 多 Agent 边界

第一波并行：

| Agent | 文件所有权 | 任务 |
| --- | --- | --- |
| A | `postgres_checkpoint.py`、`test_postgres_checkpoint.py` | 单连接池修复、schema 和离线生命周期测试 |
| B | 新的真实集成测试文件 | setup、单连接池、upsert、终态和 TTL 真实覆盖 |
| C | 新的 PostgreSQL 专项 PowerShell 脚本及独立脚本测试 | dry-run、脱敏、报告和执行步骤 |

第二波串行：

- 集成 Agent 合并 A/B/C。
- 更新中央 `verify-langgraph-real-gate.ps1` 和 `ai-validation-scripts.tests.ps1`。
- 更新 README、启动文档和交接文档。
- 运行完整默认门；具备人工环境后再运行真实 PostgreSQL 门。

禁止多个 Agent 同时修改中央门禁或交接文档。

## 9. 人工接入点

以下动作必须由用户执行或明确授权：

1. 选择本机 PostgreSQL 实例，确认不是 Spring MySQL 或其他产品的数据库。
2. 创建或确认专用角色和数据库 `yu_ai_checkpoint`。
3. 将连接 URL 注入本地进程环境或密钥管理；不得发送到聊天或提交 Git。
4. 决定是否允许停止数据库、断网或 kill backend 做故障注入。
5. 生产环境决定 `AUTO_SETUP=false`、最小权限、备份和监控策略。

Agent 可以提供命令模板，但不得读取容器环境变量获取密码，不得自动修改或重启用户容器。

## 10. 非目标

- 不实现工作流断点续跑或新增 resume API。
- 不启用 `PostgresStore`、向量检索或长期记忆。
- 不迁移 Spring MySQL、Redis 工具幂等或项目文件所有权。
- 不保留 Redis/PostgreSQL 双 checkpoint 后端。
- 不删除 Legacy，不提高灰度比例，不引入业务多 Agent。
- 不把真实 DSN、密码、checkpoint payload、源码或提示词写入报告。

## 11. 完成标准

- 单连接池风险有回归测试，离线测试先失败后通过。
- Python 全量测试、compileall、lock check 通过。
- Windows PowerShell 5.1 和 PowerShell 7 的专项脚本测试通过。
- 默认统一门通过，未执行的真实 PostgreSQL 明确标记为 not-run/blocked，而非冒充 passed。
- 用户准备独立数据库后，真实 PostgreSQL 集成不再 skipped，专项报告为 passed。
- 交接文档明确区分“checkpoint 存储实现完成”和“显式工作流恢复协议未实现”。
- 工作区不包含秘密、用户项目修改或未授权容器变更。
