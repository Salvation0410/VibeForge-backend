# PostgreSQL 子图恢复与清理验收计划

**目标：** 验证现有子图在真实 PostgreSQL 中跨连接池重建恢复及完整清理，不新增生产恢复入口。

**方案：** 复用已有 Vue 批次模型与断点辅助函数；测试使用唯一 thread，关闭原连接池后以新 saver 重建父图，检查工具 ID、计数与父子状态。数据库中其他请求不作为测试断言或删除目标。

**技术栈：** pytest、LangGraph、AsyncPostgresSaver、psycopg。

- [x] 新增 opt-in 集成测试文件；复用现有 `AI_SERVICE_POSTGRES_INTEGRATION` 开关与 Settings 连接配置。
- [x] 参数化 Vue 生成/修复、模型/首个工具完成断点；跨连接池恢复后验证不重复已完成工作、预算和私有状态隔离。
- [x] 验证成功、失败、取消与 TTL 后的父子 checkpoint、blob、pending write 清理；检查另一活跃 thread 保留。
- [x] 执行真实 PostgreSQL 专项、compileall、全量 pytest、uv lock --check，记录失败原因。
- [x] 更新 README 和交接文档，仅提交本次文件并报告验证及提交哈希。
