# Spring 生成任务账本实施计划

**目标：** 将生成请求归属和执行终态持久化，并提供隔离实时租约与发布证据的查询。

**架构：** Controller -> AppService 权限边界 -> GenerationTaskService -> MyBatis Mapper/MySQL；ArtifactPathResolver 只读核对版本存储。已有 Redis 状态门继续仲裁取消与提交。

- [x] 新增实体、VO、Mapper/XML、初始化和增量建表 SQL；实现请求唯一插入、RUNNING 条件终态和取消更新、30 天查询保留。
- [x] 接入生成生命周期：先落任务再调用历史/模型，终态 best-effort，重复订阅使用新 UUID，获胜取消记录墓碑。
- [x] 新增所有者查询接口与静态提交证据只读核对，保持原 status/SSE。
- [x] 添加 SQL 和服务生命周期/权限/发布证据测试；运行干净编译及相关 Java 测试。
- [x] 更新 README/交接及部署限制，检查差异，仅提交本次相关文件。
