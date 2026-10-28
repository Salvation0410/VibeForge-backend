# 持久化取消与静态发布仲裁实施计划

**Goal:** 让已确认取消在 Redis 状态丢失后仍禁止静态活动版本切换。

**Architecture:** 在既有 MySQL 代际行锁下串行化请求签发、取消与提交资格；Redis 维护实时镜像，发布事实独立保存。

**Tech Stack:** Java 21、Spring TransactionTemplate、MyBatis XML、MySQL、H2、JUnit、Mockito。

- [x] 新增 `sql/alter_generation_arbitration.sql` 与初始化表；扩展 GenerationFenceMapper 请求绑定、决策读取和更新 SQL。
- [x] 在 GenerationTaskMapperTest 用真实 XML 增加取消先赢、提交先赢、并发竞争、旧请求拒绝及服务重建测试。
- [x] GenerationFenceService 签发原子绑定 requestId，增加 cancel/beginCommit 仲裁，activate 复核；所有数据库错误脱敏。
- [x] GenerationLeaseService 接入数据库仲裁，Redis 镜像写失败继续取消通知；提交占位失败不调用 action，不逆转持久化占位。
- [x] 任务查询补充权威取消时间；启动后检查仲裁状态，覆盖任务尚未插入的窗口。
- [x] 更新 MySQL 夹具预检、相关单元测试、账本文档与交接第 16 节。
- [x] 执行 `mvn clean -DskipTests compile` 和限定账本/租约/发布/取消/网关回归；执行 `git diff --check`，仅提交本任务文件，提交信息使用中文。

本轮在当前干净 dev 分支串行执行；用户已授权实现，无需再次询问执行方式。不执行无筛选真实模型测试或未经确认的数据库 DDL。

验证结果：干净编译成功；限定回归 154 项执行，144 通过，10 项真实资源 opt-in 跳过。真实库迁移未执行。
