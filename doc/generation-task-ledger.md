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

## 验证与后续

相关测试覆盖真实 Mapper XML 的请求唯一性、并发终态 CAS、取消幂等、查询保留与归属；服务测试覆盖三类生成、任务插入失败关闭、断连取消、早期取消、终态与实时证据隔离；临时目录测试验证提交证据和版本保留清理。测试不访问真实模型、业务 MySQL、Redis 或真实生成项目。

后续应先完成真实 MySQL/Redis/文件系统联调与中断矩阵，再设计恢复认领协议：任务执行所有权/心跳/过期判定、持久化取消权威状态、Spring 发布事实核对和新的租约获取必须一起定义；最后增加 Python checkpoint 续跑与客户端重连。不能仅根据 RUNNING 或 `ainvoke(None)` 自动续跑。
