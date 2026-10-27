# 生成租约代际与活动版本 fencing

## 目标

让丢失 Redis 租约的旧生成执行者无法把 HTML 或 MULTI_FILE 产物切换为活动版本。MySQL 保存每应用单调递增的 fencing token、每次已提交发布的事实和每种生成类型当前活动版本；共享文件系统继续保存不可变 release。

## 状态与数据流

1. `GenerationLeaseService.acquire` 成功取得 Redis 应用锁后，在 MySQL 为该 app 原子递增代际；代际写入 `GenerationLease`。代际分配失败时释放 Redis 锁并拒绝执行。
2. `generation_task` 在模型调用前保存同一 token。请求 ID 仍唯一，token 不承载用户输入或源码。
3. 发布先在共享文件系统完成 staging、摘要复核、验证、不可变 release 和墓碑；随后 MySQL 事务锁定该 app 的当前代际，只有发布 token 等于当前代际时，才登记不可变发布事实并切换 `(appId, codeGenType)` 活动指针。失败时旧活动指针不变。
4. `ArtifactPathResolver` 对启用数据库指针的应用从 MySQL 解析活动 release。数据库指针提交成功后，文件 `.current` 仅作为兼容镜像；镜像迟到或失败不得改变服务实际读取的版本。
5. 预览、部署、下载、上下文读取和工具网关共用解析器。旧项目根目录在 fencing 模式启用前仍按既有 `.current`/平铺目录规则读取。启用数据库指针后数据库不可用则失败关闭，不回退到可能过期的文件指针。
6. 发布状态查询先使用 MySQL 不可变发布记录确认 fencing 发布，再复核文件证据；历史发布事实不随活动指针切换或 release 保留清理而消失。

## 数据边界

- 新增每应用 fencing 代际表、不可变发布账本和每类型活动版本表；`generation_task` 增加 `fence_token`。
- MySQL 迁移单独提供增量脚本，并同步首次初始化 SQL。部署前必须执行新迁移。
- 数据库发布事务校验 `generation_fence.current_token == request.fence_token`，并在同一事务写入发布事实与活动指针。请求旧于当前 token 时以稳定错误码拒绝。
- 文件系统与 MySQL 仍不是跨资源事务：失败可留下未激活 release/墓碑，但活动指针只能由成功的 MySQL fencing 事务决定。
- fencing release 序号使用数据库 token，暂不自动执行 release 保留清理，避免暂停后恢复的旧发布者误删新活动版本；后续保留调度必须具有自己的数据库保护。部署时所有实例统一升级；回滚先停止生成和旧执行者，并按 DB 权威指针核对文件镜像。
- Redis 仍负责取消与当前执行互斥；MySQL 代际负责保护活动版本。该设计不实现任务恢复、重试、心跳或 SSE 重放。
- 管理员人工恢复也必须取得 Redis 租约和新的 MySQL 代际，不能绕过 fencing。

## 验收

- 真实 Mapper SQL 覆盖代际并发递增、任务 token 保存、当前代际 CAS、发布账本与活动指针原子更新。
- 两个 Redis 客户端配合真实 MySQL、临时共享发布目录：旧租约失效后新代际发布成功，旧代际迟到发布被拒绝且不能改变活动版本；镜像迟到不影响解析结果。
- 注入数据库不可用、发布账本提交失败、文件镜像失败，确认不发生错误激活且已提交的数据库指针保持权威。
- 运行 Java 编译、相关单测、真实资源 opt-in 测试（若本机资源可用）、`git diff --check`。

## 不在本轮范围

Python 子图拆分、自动恢复/认领、真实模型、端到端 SSE，以及 Vue 工程目录的构建版本 fencing。
