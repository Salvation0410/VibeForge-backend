# B0b Java 权威发布实施计划

目标：按已批准设计完成 MySQL 权威事实与 Java 调度边界。
架构：epoch 单行串行管理变更及发布；候选生产在事务外；commit 原子切 head、serving、任务和 alias outbox。

- [x] 先添加收据重试、冲突、claim/租约/epoch 拒绝的失败测试。
- [x] 新增数字精确 DTO、publication Mapper/XML 与迁移 SQL。
- [x] 实现 prepare/commit/read/fact 的短事务及租约锁定校验。
- [x] 整合 worker、管理变更 epoch、内部 Bearer 与维护 bootstrap。
- [x] 周期对账当前 head，过时事件标记 SUPERSEDED，当前镜像成功才 ACK。
- [x] 执行普通测试、隔离 MySQL 事务测试、干净编译与 diff 检查；提交交由父代理统一执行。
