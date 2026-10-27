# 生成任务账本与查询

Spring 在 MySQL `generation_task` 表记录从 `/api/apps/chat/gen/code` 发起的生成任务，覆盖 HTML、MULTI_FILE、VUE_PROJECT 和 Legacy/LangGraph 两条引擎链路。人工产物恢复、类型路由等其他入口不创建该记录。Python 的 `ai_workflow_status` 仍是脱敏节点摘要，不是此业务账本。

## 部署顺序

1. 在业务 MySQL 数据库执行 `sql/alter_generation_task.sql`；首次初始化的 `sql/init_database.sql` 也包含同一张表。
2. 升级 Spring。代码不会自动执行迁移，未建表或任务插入失败时生成在历史/模型调用前失败，返回 `GENERATION_TASK_UNAVAILABLE`。重复请求唯一键冲突返回 `GENERATION_REQUEST_EXISTS`，不再次调用模型。
3. 回滚 Spring 时保留该表即可；无需改变 Python、Redis、前端配置，也没有新增环境变量。

本阶段未执行真实 MySQL 迁移。迁移脚本使用 MySQL 的 ASCII 二进制请求 ID 主键、DATETIME(6)、索引与执行状态 CHECK；H2 MySQL 模式测试只验证业务 SQL 与约束行为，不能证明部署数据库的方言、权限或版本兼容。

## 记录生命周期

应用所有权和类型验证通过后，每次实际订阅分配新的服务端 UUID，并获取既有应用租约；任务插入成功后才写用户历史、调用模型。表仅记录请求/应用/用户标识、生成类型、执行状态、稳定错误码和时间，不记录提示词、源码、供应商异常正文或令牌。应用删除后记录保留，但查询仍要求现存应用所有权；不提供管理员跨用户读取入口。

执行状态为 `RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED`，终态仅允许从 RUNNING 条件更新，迟到回调不能覆盖已有终态。取消仍由 GenerationLeaseService 的 Redis 状态门与发布互斥仲裁；取消获胜后记录 `cancelRequestedAt`，重复取消不会改写首次时间。若引擎先记录终态，取消线程随后仍可补首次取消观测，但不改变终态或延长查询保留期。租约获得至任务插入之间发生的取消在插入后复核并补记，随后停止进入历史/模型调用。取消时间表示获胜取消请求被观察到，不代表引擎已经停止。浏览器断开连接后仍等待引擎真实终态再释放租约。

任务终态保留 30 天查询窗口（`queryUntil`）；RUNNING 无自动查询截止时间。当前没有物理清理调度，过期记录仍保存请求唯一性和取消墓碑，不能直接删除后允许旧请求重建。表不使用逻辑删除或外键级联，也没有社区审核字段。

终态与取消记录是 best-effort：MySQL、Redis 与产物文件之间没有跨资源事务。终态写失败不会把已发布成功改报为失败；记录可能停留在 RUNNING。取消仲裁与墓碑写入之间失败也可能缺少取消时间。本阶段账本不能授权续跑，生产恢复入口继续关闭。

## 查询接口

登录后请求 `GET /api/apps/chat/gen/code/task?appId=42&requestId=<请求ID>`。省略 requestId 时返回当前用户在该应用最近一个未超过查询保留期的任务。查询检查登录用户、现存应用所有权、任务 appId 和任务 userId。不存在、超出保留期或不属于该查询范围时返回统一业务错误；路径穿越和空请求 ID 作为参数错误拒绝。返回 Spring `BaseResponse<GenerationTaskVO>`，查询/活动状态/取消子资源的鉴权错误也返回 JSON，生成流的 SSE 保持原协议。

主要字段：

| 字段 | 含义 |
| --- | --- |
| requestId / appId / codeGenType | 请求标识、应用及生成类型 |
| executionState / errorCode | Spring 观察到的执行终态与脱敏稳定代码 |
| cancelRequestedAt | 取消请求获胜的观测时间，允许为空 |
| createdAt / updatedAt / finishedAt / queryUntil | 任务时间与终态查询截止时间 |
| leaseState | 同一 requestId 当前 Redis 状态；不匹配、已释放或 Redis 不可用均为 UNKNOWN |
| publicationState | COMMITTED、NOT_CONFIRMED、UNAVAILABLE 或 NOT_VERSIONED |

HTML/MULTI_FILE 的 COMMITTED 来自 VersionedArtifactStore 的提交标记与发布墓碑；若 release 仍保留，再核对 manifest、序号、文件集和哈希。仅有墓碑不构成成功证明，因为它在指针切换前写入。版本被保留策略清理后，提交标记与墓碑仍可证明历史提交；COMMITTED 不代表该版本仍是当前活动版本。证据缺失为 NOT_CONFIRMED，证据损坏/读取异常为 UNAVAILABLE。旧版无提交标记的历史发布不作成功推断。Vue 尚无不可变版本发布证明，返回 NOT_VERSIONED。

执行状态、租约和发布证据分别读取，不是原子快照。FAILED 与 COMMITTED 可以同时存在（例如发布后传输失败），无租约也不能证明执行失败。查询不回写任务状态、不修复当前指针、不调用 Python、不自动恢复。原 `/chat/gen/code/status` 继续返回活动生命周期，不以任务终态替换其 IDLE 语义；公共 SSE 不新增事件。

## 真实资源验证与后续

真实资源验收测试位于 `src/test/java/com/yupi/yuaicodemother/core/artifact/GenerationTaskResourcesIT.java`，默认跳过。仅当目标配置是 `localhost` 的业务库 `yu_ai_code_mother` 和 Spring Redis database 1，且已显式设置进程环境变量 `AI_TASK_INTEGRATION=true` 时执行：

```powershell
$env:AI_TASK_INTEGRATION = "true"
mvn test '-Dtest=GenerationTaskResourcesIT'
```

夹具使用 application.yml/local 配置与标准 Spring 环境变量覆盖，预检账本表但不迁移；不打印连接凭据，不启动/停止/清空服务。测试写入随机应用 ID 和唯一请求 ID，结束后只删除精确任务行和三个对应 Redis 键，发布产物位于 JUnit 临时目录。不要在连接非本地库/Redis 或用户共享 fixture 的本地配置下开启该测试。

测试覆盖真实 Mapper XML、数据库唯一约束和并发终态 CAS，插入确认丢失留下可查 RUNNING、取消落账失败但 Redis 仍禁止发布、发布后终态写失败与重建服务查询、丢失租约及后继租约安全。测试模型、文件构建、Spring/Python HTTP 仍由 fake 或局部临时目录替代。

真实资源测试发现并修复 GenerationLeaseService 两个问题：其一，Redis 租约锁丢失但 ACTIVE 字符串残留时提交仍能执行；其二，Redisson 默认可重入使同客户端同线程可获取第二个同应用请求并覆盖状态。现在状态变更互斥锁保护租约获取和状态初始化，重入在 tryLock 前拒绝；提交执行前检查应用锁仍存活；取消和状态查询不会把无锁残留状态当成活动请求。检查与文件发布之间仍有 TOCTOU 窗口，Redis 锁到期/失联发生在实际文件写入期间时，本阶段没有 fencing token 阻止迟到写入；不得声称解决发布中的租约过期。

纯测试覆盖真实 Mapper XML 的请求归属、取消与终态顺序、查询保留；服务测试覆盖三类生成、失败关闭和错误脱敏；临时目录测试验证发布证据和版本保留清理。真实提交 SQL 没有在产品路径外额外开启，真实文件写只位于系统生成临时目录。

恢复调度仍未实现。下一阶段应设计持久化执行所有者、心跳和过期判定、跨资源 fencing/发布幂等、取消权威状态和恢复认领协议；接着验证 Python checkpoint 续跑及客户端重连。不能仅根据 RUNNING、无锁或 `ainvoke(None)` 自动续跑。
