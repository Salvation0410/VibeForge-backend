# 客服知识任务并发与条件落账实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 证明现有MySQL任务owner/版本条件的真实事务边界，并修复DELETE/REBUILD回执CAS失败仍记录成功的问题。

**Architecture:** 保持既有outbox/serving协议，终态以条件写命中一行为依据；旧owner返回0视为失去claim，数据库写异常视为落账不确定，不由执行异常路径立刻再写失败/重试。使用隔离真实MySQL schema、生产Mapper/Finalizer和事务代理验证多连接接管与回滚。

**Tech Stack:** Java21、JUnit5、MyBatis、Spring事务、MySQL、Mockito。

授权依据：用户要求交接后直接进行下一轮，已批准路线B的版本正确性与迟到owner/丢响应边界；入口为 `doc/customer-service-rag-rounds-handoff.md`。本轮不实现完整B0b、不切换生产alias、不收费调用。

## Task 1：worker回执落账

Files：`src/main/java/com/yupi/yuaicodemother/worker/CustomerServiceKnowledgeEtlWorker.java`、`src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceKnowledgeEtlWorkerTest.java`。

- [x] 新增确定性回归：DELETE/REBUILD成功RPC后finish返回0、抛数据库异常、成功1三类。捕获固定日志确认CAS为0或异常没有task succeeded；不调用retry/failIndex，不覆写新owner。
- [x] 先运行单测确认现代码失败：`mvn test -Dtest=CustomerServiceKnowledgeEtlWorkerTest -q`。
- [x] 以单独辅助方法包装非INDEX终态：`int changed = outboxMapper.finish(task.getId(), owner, "SUCCEEDED", null)`；changed!=1抛LostClaimException；Mapper异常包装TaskFinalizationException，交给既有专门catch仅记录不确定。成功日志只有上述检查后可达。
- [x] 保留finally仅撤销当前执行持有的operationId/fence租约；不撤销替代执行，不重算索引代际。
- [x] 再跑同单测，核对旧测试mock默认成功行数及新增失败边界。

## Task 2：真实MySQL并发/事务验收

Files：新增 `src/test/java/com/yupi/yuaicodemother/customerservice/KnowledgeOwnershipMysqlIntegrationTest.java`；复用 `KnowledgeRevisionMysqlSupport` 读取私有datasource，不复制/打印凭据。

- [x] 显式开关 `AI_KNOWLEDGE_MYSQL_OWNERSHIP_INTEGRATION=true`，默认skip。只创建随机 `rag_owner_it_` schema及知识document/outbox/guard/lease表，finally严格核对名称后清理；业务库只作为认证连接来源。
- [x] 使用真实Mapper、两个独立连接/事务，CountDownLatch屏障竞争claim，断言只有一个owner成功。
- [x] 用固定时间构造过期接管，证明旧owner的finish/retry/refreshClaim返回0、新owner不变；不靠sleep等待过期。
- [x] 用真实Spring事务代理调用Finalizer，构造旧owner文档更新后outbox CAS失败，核对整个事务回滚；当前owner成功时文档和task同时落账。
- [x] RPC已完成但回执丢失用持久PROCESSING模拟，不伪造真实网络通过；接管后迟到完成不能覆盖新owner。禁用/版本推进/激活推进使旧INDEX不能重新标ACTIVE。
- [x] 两个Coordinator实例共享真实Mapper和DB事务，证明collection/document互斥、过期/撤销和旧proof有效但current=false；不冒称MySQL/Milvus原子发布。
- [x] 运行显式测试并记录计数、清理结果；任何失败先定位，不改成仅Mock或放宽断言。

## Task 3：复核、文档与交付

- [x] 分别进行规格与代码质量复核，检查实际SQL而非只看测试名称。
- [x] 干净编译及知识相关Java回归；Python无行为改动不为文档重复全量或收费模型。
- [x] 更新逐轮交接、旧详细交接、相关README和本计划勾选，明确这只是B0b前置；排空检查后已重启授权管理的Spring并核对健康和原索引不变。
- [x] 明确下一轮B0b完整流程：稳定publicationId/私有不可变候选、Spring epoch条件提交权威指针、读路径使用权威目标、alias outbox对账；列出丢响应、迟到owner、检查后暂停、迁移/回退及缺资源边界。
- [x] git diff --check，只提交本次代码/测试/文档，保留用户既有README格式修改与无关计划；中文Conventional Commit并报告哈希。

执行结果：源码提交 `02b88e5`；红测4失败→worker30通过；最终99普通+7真实MySQL共106通过，干净编译通过，最终7随机schema清理确认。已部署Spring回执修复，72活动块与原alias保持不变。详见逐轮交接第二轮。
