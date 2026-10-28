# 生成任务账本与查询

Spring 在 MySQL `generation_task` 表记录从 `/api/apps/chat/gen/code` 发起的生成任务，覆盖 HTML、MULTI_FILE、VUE_PROJECT 和 Legacy/LangGraph 两条引擎链路。人工产物恢复、类型路由等其他入口不创建该记录。Python 的 `ai_workflow_status` 仍是脱敏节点摘要，不是此业务账本。

## 部署顺序

1. 在业务 MySQL 数据库先执行 `sql/alter_generation_task.sql`，再执行 `sql/alter_generation_fencing.sql`、`sql/alter_generation_arbitration.sql` 与 `sql/alter_generation_execution_heartbeat.sql`；首次初始化的 `sql/init_database.sql` 已包含完整 schema。
2. 升级 Spring。代码不会自动执行迁移；缺少任务账本、fencing、仲裁表或所有者/心跳列时，租约建立或任务开始失败关闭，不进入模型调用。重复请求唯一键冲突返回 `GENERATION_REQUEST_EXISTS`，不再次调用模型。
3. 所有 Spring 实例必须切换到支持 DB 指针的版本后再开放生成；不能混跑仍以 `.current` 为权威的旧实例。无 Python、Redis、前端配置迁移，也没有新增环境变量。回滚时先停止生成和旧执行者，按数据库活动指针核对并导出文件镜像，再回退旧代码；只保留表不构成安全回滚。

`sql/alter_generation_fencing.sql` 为已执行过任务账本迁移的数据库增加 token 列及 fencing 表；列存在性由 `information_schema` 条件判断，重复执行安全。2026-10-27 已在本地 MySQL 8.0.41 的 `yu_ai_code_mother` 执行并核验 fencing 列和三张表；H2 MySQL 模式测试仍只验证 Mapper SQL 与 CAS 语义。

## 记录生命周期

应用所有权和类型验证通过后，每次实际订阅分配新的服务端 UUID，并获取既有应用租约。Redis 锁成功后，MySQL 在同一事务为应用递增 fencing token 并向 `generation_arbitration` 插入唯一请求绑定，token 写入租约和任务账本；任务插入成功后才写用户历史、调用模型。账本仅记录请求/应用/用户标识、生成类型、执行状态、token、随机执行所有者、稳定错误码和时间，不记录提示词、源码、供应商异常正文或令牌。应用删除后记录保留，但查询仍要求现存应用所有权；不提供管理员跨用户读取入口。

执行状态为 `RUNNING`、`SUCCEEDED`、`FAILED`、`CANCELLED`，终态仅允许从 RUNNING 条件更新，并匹配本实例的执行所有者及原 fence token；无本实例执行登记的回调不写终态，迟到回调不能覆盖已有终态。取消与静态提交资格由 MySQL `generation_arbitration` 权威仲裁：两者先锁应用代际行，再以当前请求与 token 检查 ACTIVE 状态。取消先赢时永久保存 CANCELLED 和首次取消时间；提交先赢时永久保存 COMMITTING，随后取消不再胜出。最终静态激活再次核对相同请求绑定、当前代际和取消决策；Redis 状态丢失或回退为 ACTIVE 也不能覆盖已确认取消。

COMMITTING 只代表提交资格，不代表产物已发布；发布事实仍查询 `generation_publication`。发布动作失败或提交结果不确定时不自动撤销该占位。数据库无法确认仲裁时返回脱敏稳定错误，不承诺取消成功，也不调用发布动作。Redis 继续提供活动请求发现、入口串行化和实时状态镜像；取消已持久化后镜像写失败仍通知引擎停止。镜像状态可能滞后，任务查询独立补读仲裁表取消时间。任务插入前的已确认取消在插入后再次复核，随后停止进入历史/模型调用。取消时间不代表引擎已经停止；浏览器断开连接后仍等待引擎真实终态再释放租约。

任务终态保留 30 天查询窗口（`queryUntil`）；RUNNING 无自动查询截止时间。当前没有物理清理调度，过期记录仍保存请求唯一性和取消墓碑，不能直接删除后允许旧请求重建。表不使用逻辑删除或外键级联，也没有社区审核字段。

任务终态及任务表的取消时间补记仍是 best-effort；仲裁表的取消决策先提交后确认，查询优先使用同请求、同应用、同 token 的仲裁取消时间。MySQL、Redis 与产物文件之间没有跨资源事务。终态写失败不会把已发布成功改报为失败；记录可能停留在 RUNNING。事务确认丢失时调用方失败关闭，持久化结果须重新查询，不根据异常猜测取消或发布未发生。仲裁记录永久保留，不自动清理或回填旧任务；旧请求缺少绑定时拒绝提交。本账本不能授权续跑，生产恢复入口继续关闭。

## 查询接口

登录后请求 `GET /api/apps/chat/gen/code/task?appId=42&requestId=<请求ID>`。省略 requestId 时返回当前用户在该应用最近一个未超过查询保留期的任务。查询检查登录用户、现存应用所有权、任务 appId 和任务 userId。不存在、超出保留期或不属于该查询范围时返回统一业务错误；路径穿越和空请求 ID 作为参数错误拒绝。返回 Spring `BaseResponse<GenerationTaskVO>`，查询/活动状态/取消子资源的鉴权错误也返回 JSON，生成流的 SSE 保持原协议。

主要字段：

| 字段 | 含义 |
| --- | --- |
| requestId / appId / codeGenType | 请求标识、应用及生成类型 |
| executionState / errorCode | Spring 观察到的执行终态与脱敏稳定代码 |
| cancelRequestedAt | 持久化仲裁首次取消时间，旧记录回退任务表观测时间，允许为空 |
| createdAt / updatedAt / finishedAt / queryUntil | 任务时间与终态查询截止时间 |
| heartbeatAt / executionExpiresAt | 数据库时钟心跳及 90 秒观测期限，不是恢复授权 |
| heartbeatState | LIVE、EXPIRED、SUPERSEDED、UNKNOWN、TERMINAL，不自动改变任务终态 |
| leaseState | 同一 requestId 当前 Redis 状态；不匹配、已释放或 Redis 不可用均为 UNKNOWN |
| publicationState | COMMITTED、NOT_CONFIRMED、UNAVAILABLE 或 NOT_VERSIONED |

HTML/MULTI_FILE 的新 fencing 发布先在文件系统写不可变 release 和墓碑，再由 MySQL 事务校验请求 token 仍是当前应用代际，并原子写入 `generation_publication` 历史和 `generation_active_artifact` 活动指针。只有此事务决定活动版本；`.current` 是兼容镜像，迟到覆盖不改变解析结果。活动指针模式通过项目根目录 `.db-pointer-enabled` 标记启用，首次启用会先把升级前 `.current` 种为 token 0 基线。解析器在该模式下从 MySQL 读指针并核验 release；MySQL 不可用或指针目标损坏时失败关闭，不回退到文件镜像。旧项目未启用该标记时仍按既有 `.current`/平铺目录读取。

新 fencing 发布的 COMMITTED 以 MySQL 不可变发布记录为依据，并复核墓碑/manifest/哈希；release 被保留策略清理后，数据库发布历史和文件墓碑仍可证明历史提交。COMMITTED 不代表该版本仍是当前活动版本。旧版无 fencing 记录的历史发布继续使用原提交标记与墓碑规则。Vue 尚无不可变版本发布证明，返回 NOT_VERSIONED。

执行状态、租约和发布证据分别读取，不是原子快照。FAILED 与 COMMITTED 可以同时存在（例如发布后传输失败），无租约也不能证明执行失败。查询不回写任务状态、不修复当前指针、不调用 Python、不自动恢复。原 `/chat/gen/code/status` 继续返回活动生命周期，不以任务终态替换其 IDLE 语义；公共 SSE 不新增事件。

## 真实资源验证与后续

真实资源验收测试位于 `src/test/java/com/yupi/yuaicodemother/core/artifact/GenerationTaskResourcesIT.java`，默认跳过。仅当目标配置是 `localhost` 的业务库 `yu_ai_code_mother` 和 Spring Redis database 1，且已显式设置进程环境变量 `AI_TASK_INTEGRATION=true` 时执行：

```powershell
$env:AI_TASK_INTEGRATION = "true"
mvn test '-Dtest=GenerationTaskResourcesIT'
```

夹具使用 application.yml/local 配置与标准 Spring 环境变量覆盖，预检任务账本和 fencing 表但不迁移；不打印连接凭据，不启动/停止/清空服务。测试写入随机应用 ID 和唯一请求 ID，结束后只删除该 app 的测试账本/代际/发布记录与三个对应 Redis 键，发布产物位于 JUnit 临时目录。不要在连接非本地库/Redis 或用户共享 fixture 的本地配置下开启该测试。

2026-10-27 最终限定回归 145 项全部通过，包含 9 项真实 MySQL 8.0.41、Redis database 1 和 JUnit 临时发布目录测试。覆盖真实 Mapper XML、唯一约束和并发终态 CAS、token 发放和发布 CAS；插入确认丢失留下可查 RUNNING、取消落账失败但 Redis 仍禁止发布、发布后终态写失败与重建服务查询、丢失租约及后继租约安全。另验证旧发布在文件校验阶段暂停、后继新代际提交后，旧发布恢复不能激活；即使 `.current` 被迟到写回旧 ID，解析器仍返回 MySQL 指向版本。该限定集合中的模型、文件构建、Spring/Python HTTP 使用 fake 或临时目录，不代表生产端到端验收。

GenerationLeaseService 保留 Redis 实时入口，持久化仲裁决定取消与提交资格；MySQL fencing token 封闭了租约检查到活动版本切换之间的 TOCTOU：release 写完后，最终 DB 事务锁定代际行并要求 token 等于当前值；新请求递增 token 与发布提交由同一 MySQL 行串行化。旧发布即使稍后覆盖 `.current`，预览/部署/下载的解析器仍跟随 DB 指针。文件系统 release 与 MySQL 提交仍非跨资源事务，失败可能遗留未激活 release/墓碑；活动指针不会因其残留改变。Vue 构建目录未纳入该版本指针协议。

fencing release 使用数据库 token 作为 manifest 序号，不并发读写旧 `.publication-sequence`。新协议暂不自动删除 release：DB 提交后旧进程仍可能暂停，恢复后不能按自己的旧活动 ID 清理其他版本。旧文件协议保留原有当前加两个历史版本策略；新协议的受保护保留调度尚未实现，需预留磁盘空间。`.db-pointer-enabled` 与 fencing 记录不得在运行中手工删除或重置。

纯测试覆盖真实 Mapper XML 的请求归属、取消与终态顺序、查询保留；服务测试覆盖三类生成、失败关闭和错误脱敏；临时目录测试验证发布证据和版本保留清理。真实提交 SQL 没有在产品路径外额外开启，真实文件写只位于系统生成临时目录。

恢复调度仍未实现。取消权威仲裁已实现；下一阶段需要设计持久化执行所有者、心跳和过期判定、恢复认领协议；接着验证 Python checkpoint 续跑及客户端重连。不能仅根据 RUNNING、无锁或 `ainvoke(None)` 自动续跑。

## 持久化仲裁升级（2026-10-28）

升级前排空旧实例请求，执行 `sql/alter_generation_arbitration.sql`，再统一升级所有 Spring 实例。该表无自动迁移，不为历史请求回填执行授权；人工 HTML 恢复也通过新租约原子绑定请求。新表缺失会使新租约和账本查询失败关闭。2026-10-28 已在本机 MySQL 8.0.41 的 yu_ai_code_mother 执行该 DDL，核验五列、ascii_bin 请求键、代际唯一索引及 CHECK 已启用。随后仲裁/Mapper/租约回归 32 项全部通过，其中真实资源 10 项。真实资源测试夹具已增加表预检及精确测试行清理。

显式取消仍依赖可发现的 Redis 活动请求及现存应用所有权；Redis 完全不可用或无租约时没有新增离线取消/恢复入口。Vue 文件修改不在静态不可变发布事务内，取消仍为协作式，不能保证在途文件副作用立即停止。没有新增 SSE 事件、Python checkpoint 接续、恢复调度或客户端重连。

## 执行所有者与心跳观测（2026-10-28）

新任务由 `GenerationTaskServiceImpl` 以随机 UUID 标识本服务实例，只在任务 INSERT 确认成功后加入内存续写登记，不扫描数据库 RUNNING 行，不因进程重建自动获取旧执行资格。INSERT 确认丢失时可能留下带所有者但没有实际执行的行，期限届满后仅呈现 EXPIRED，不能推断是否调用过模型。execution_owner 不通过任务 VO 返回，也不保存主机名、IP 或凭据。

独立 `generationTaskHeartbeatScheduler` 每 15 秒续写已登记任务。显式保留默认 `taskScheduler` 供客服 ETL 等无限定调度名的任务使用，避免默认任务阻塞心跳。每次续写匹配 requestId/appId/owner/token、RUNNING、尚未过期且仍是当前应用代际。heartbeat_at 与 execution_expires_at 均来自数据库时间，期限为 90 秒；超时不会被随后恢复运行的旧执行者复活。数据库瞬时异常可在期限内重试，条件更新失败则停止该任务续写。完成、失败或取消的真实终态回调无论落账成功与否都会删除续写登记；浏览器断开及发出停止请求不提前停止心跳。

任务查询在原权限与保留期过滤下，用同条 SQL 计算 heartbeatState：非 RUNNING 为 TERMINAL；缺少所有者、心跳或期限为 UNKNOWN；当前应用代际已不同为 SUPERSEDED；期限已到为 EXPIRED；否则 LIVE。历史行不回填所有者/心跳。查询不写回执行状态，不修改取消或发布事实。SUPERSEDED/EXPIRED 仅表示不能再续写心跳；原所有者对同请求、同 token 的真实终态仍可条件落账，不把超时等同执行失败。

该心跳只证明 Spring 执行控制器仍在续写，不保证 Python/模型正在前进，也不替代 Redis 锁与发布 fencing。没有新增恢复认领、心跳超时取消、自动重试、Python checkpoint 续跑或 SSE 重连。未来认领必须同时改变执行所有者与代际，并把身份传到工具调用、发布和终态回写；不能仅据 EXPIRED 或无 Redis 锁认领。

部署前执行 `sql/alter_generation_execution_heartbeat.sql`，历史三列允许为空；迁移使用 information_schema 条件 DDL，兼容 MySQL 8.0.41。2026-10-28 已在本机业务库执行并再次执行验证幂等，三列原先不存在，迁移后确认 execution_owner 为 varchar(36)/ascii_bin、两个时间为 datetime(6)，均允许 NULL。没有自动迁移，升级前应排空旧实例执行并统一切换；本轮未重启现有 Spring/Python 服务。

本阶段最终验证：干净编译成功；开启真实资源 opt-in 的限定账本、租约、发布、工具、网关与心跳回归 163 项全部通过，含真实 MySQL/Redis 12 项。调度上下文证明默认任务阻塞不影响独立心跳；未运行真实模型、Python HTTP 或浏览器端到端。
