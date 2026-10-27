# 生成租约代际 Fencing 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 通过 MySQL 单调代际和权威活动版本指针阻止失效生成执行者激活静态产物。

**架构：** Redis 继续仲裁请求生命周期；租约取得时分配 MySQL fencing token，写入生成任务。不可变文件 release 落盘后，MySQL 在锁定代际行的事务内记录发布事实并切换活动指针；共享文件系统的 `.current` 只保留兼容用途。所有活动版本读取仍经 `ArtifactPathResolver`。

**技术栈：** Java 21、Spring Boot、MyBatis-Flex/XML、MySQL 8、Redisson、JUnit、H2 MySQL 模式。

---

### Task 1：数据库状态和租约 token

**文件：** `sql/alter_generation_fencing.sql`、`sql/init_database.sql`、GenerationFence entity/mapper/XML/service、`GenerationLease`、`GenerationLeaseService`、`GenerationTask`、task mapper/service/XML。

- [x] 加 fencing 代际、发布事实、活动指针表及 `generation_task.fence_token`；迁移脚本可重复安全执行。
- [x] 通过数据库事务递增每应用 token；租约签发失败时释放 Redis lock。
- [x] 在开始生成任务时保存租约 token；人工恢复路径也创建新代际。
- [x] H2 测试 token 单调递增、任务 token 落账和事务 fencing。

### Task 2：以 MySQL 提交发布事实与活动指针

**文件：** fencing mapper/service、`ArtifactPublicationService`、`VersionedArtifactStore`、发布测试。

- [x] 先完成不可变 release 与内容核验，再以单一 MySQL 事务检查当前 token、插入发布历史并更新活动指针。
- [x] 旧 token 返回稳定 `GENERATION_FENCE_REJECTED`；数据库事务失败时不得将文件指针视为成功。
- [x] DB 指针提交后再更新 `.current` 镜像；镜像错误只记录脱敏日志，不反转已提交结果。
- [x] 覆盖发布记录、旧 token 乱序、DB 故障边界及镜像迟到。

### Task 3：统一活动版本读取

**文件：** `ArtifactPathResolver`、fencing mapper/service、路径解析/任务状态测试。

- [x] 对 fencing 模式应用优先按 MySQL 活动指针解析 HTML/MULTI_FILE release，并验证 request ID、app ID、manifest 和文件摘要。
- [x] DB 未启用记录时继续按旧 `.current` 与平铺目录规则读取；启用后 DB 错误失败关闭。
- [x] 发布状态查询结合 MySQL 不可变发布记录及文件证据，保留被后续版本取代的历史成功事实。
- [x] 验证共享解析器在旧文件镜像被迟到写入时仍返回 DB 指定版本。

### Task 4：集成故障矩阵和交付

**文件：** `GenerationTaskResourcesIT`、相关单测、`ai-service/README.md`（仅如 Python 可见行为确有变化）、`doc/ai-service-subgraph-refactor-handoff.md`、`doc/generation-task-ledger.md`。

- [x] 增加真实 MySQL/Redis/临时共享目录测试：旧 token 失效、新 token 发布、旧 token 迟到拒绝、镜像迟到。
- [x] 执行相关 Java 测试、`mvn clean -DskipTests compile`、真实资源 opt-in 和 `git diff --check`。
- [x] 更新迁移说明、交接记录与验证边界；检查密钥、用户修改和提交范围。
- [x] 完成最终限定回归 145 项、干净编译及差异检查；以中文 Conventional Commit 提交本轮文件，哈希随交付报告。
