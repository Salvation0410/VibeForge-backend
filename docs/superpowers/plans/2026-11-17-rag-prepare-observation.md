# 第十轮准备仲裁故障验收实施计划

**Goal:** 补持久仲裁退出/确认丢失证据；单任务只读生产接口待方案确认。

**Architecture:** 复用随机 MySQL schema 与子 JVM；Python 独立 Preparer 配合有界屏障和计数替身。测试设施不部署到正式服务，正式知识库只读复核。

**Tech Stack:** Java 21/JUnit/MySQL、Python/pytest/asyncio。当前会话顺序执行，不分派代理。

- [x] 在现有 ProcessIntegrationTest 增加 prepare-begin/prepare-complete halt 点；先验证持久行再请求，核对不重授、完成幂等与无发布事实。
- [x] Python 新增 test_knowledge_prepare_execution_faults.py：下载/Embedding阶段取消、Embedding返回后构建异常、完成登记提交后丢确认、租约/epoch付费前失效。用 AsyncMock 隔离权威校验，明确非真实跨服务证明。
- [x] 运行 `uv run pytest tests/test_knowledge_prepare_execution_faults.py tests/test_knowledge_prepare_arbitration.py tests/test_spring_prepare_arbitration.py`，目标无失败，实际调用计数符合预期。
- [x] 显式 `AI_KNOWLEDGE_PUBLICATION_MYSQL_INTEGRATION=true` 运行 Process/MysqlIntegrationTest，子 JVM 必须 halt(77)，随机 schema 精确清理。
- [x] 用户确认单任务观测后，实现独立只读 Service/DTO、联合归属投影 Mapper 与管理员 GET；验证权限声明、状态边界、数据不可用、无凭据/收据读取和不写状态；未确认不修改生产行为。
- [x] 必要范围完整回归、编译、正式只读核对；逐轮交接如实记录未测项；`git diff --check` 后只提交本轮文件。

取消场景使用 Event 屏障并 wait_for(5)，不靠短 sleep 调度猜测；取消后 await 完成释放许可。完整收据回读恢复与未知执行分别计数，不能把 Fake 计数写作真实计费 exactly-once。

实际验收：Python相关六文件63通过；Java MySQL16与子JVM5共21通过，21随机schema清理。用户已确认单任务接口；续轮Java32通过、22随机schema清理，Spring已重启并通过本机接口及知识库完整核对，未重复完整Python1656基线或HTTP矩阵。
