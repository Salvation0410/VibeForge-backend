# 生成执行所有者与心跳实施计划

**Goal:** 持久化执行所有者与数据库时钟心跳，提供恢复前所需存活证据。

**Architecture:** 扩展现有任务账本，进程内登记只续写本实例确认启动的任务；终态以 owner/token CAS 写入。独立调度器隔离客服 ETL 调度。

**Tech Stack:** Java 21、Spring Scheduling、MyBatis XML、MySQL 8、H2、JUnit/Mockito。

- [x] 给 generation_task 增加 execution_owner/heartbeat_at/execution_expires_at，新增兼容 MySQL 8.0.41 的条件迁移和初始化字段。
- [x] 扩展实体、VO 与 Mapper XML：创建绑定 owner、条件心跳、终态 owner/token CAS、SQL 计算 heartbeatState。
- [x] 任务服务登记与调度：INSERT 确认后登记；15 秒心跳、90 秒期限；终态或销毁清理，续写异常脱敏且不复活超时记录。
- [x] 新增独立 scheduler bean；更新直接调用 Mapper 和服务的既有测试。
- [x] 在 H2/服务测试中验证旧 owner、旧 token、超时、新代际、确认丢失与停止续写；执行干净编译。
- [x] 执行新增列迁移并核验，增加真实 MySQL/Redis 期限与所有者测试，运行限定回归。
- [x] 同步账本文档与交接文档第 17 节，记录真实验证边界，git diff --check 后中文提交。

本轮串行执行，用户已要求继续下一阶段；不启动生产恢复调度、不清理业务数据、不接入真实模型。

实际结果：仲裁表及所有者/心跳三列均已迁移；心跳迁移再次执行成功。干净编译通过；最终限定回归 163 项全通过，含真实 MySQL/Redis 12 项；无生产认领或恢复入口。
