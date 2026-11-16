# 客服知识 v2 版本基础（B0a）迁移

`documentVersion` 是内容修订，`etlVersion` 是每文档稳定索引修订；同一 outbox 的重试复用后者。每次重建任务的顶层 `etlVersion` 仅代表 buildRevision，逐文档索引身份来自 serving 字段。UUID `operationId` 只代表单次执行租约。

新文档 `activationRevision=1`；禁用后的启用、禁用文档替换上传递增激活。禁用/删除以当前激活为墓碑上界，Python v2 删除该文档所有激活不大于该值的索引。DISABLED 不能通过 reindex 绕过启用。普通替换/重新索引保持激活，失败保留旧 serving 三元组。成功落账在同一条件 UPDATE 更新 indexedVersion、indexedEtlVersion、indexedActivationRevision。

重建查询 ACTIVE/UPLOADED/INDEXING/FAILED 中属于当前激活的 serving，按不可变原文登记读取已索引内容。LEFT JOIN 缺原文或版本字段非法时明确失败，不能静默遗漏。旧 OSS 对象不得由清理任务删除。原文注册不等于长期保留策略已实现。

## 执行顺序

1. 备份业务库和 OSS 原文，关闭知识上传、启用、禁用、重索引和重建入口；停止自动领取的新任务，等待所有旧 PENDING/PROCESSING 完成，再停止 worker。超时任务先按现有恢复流程处置，不能更换账本绕过。
2. 执行迁移前 SELECT。旧库 `indexedVersion != documentVersion` 无法从当前 objectKey 推导旧原文；即使相等，非 ACTIVE 的 serving 索引修订也不可凭当前 etlVersion 伪造。必须先在旧版恢复历史原文及索引身份，或经运维明确选择原文重新索引/停用；脚本 SIGNAL 阻断，不自动丢弃旧 serving。没有历史原文时无法承诺恢复旧内容。
3. 在支持遇错停止的 MySQL 客户端执行 `sql/alter_customer_service_knowledge_v2_revisions.sql`。该脚本仅用于未加 v2 列的旧库一次性迁移；DDL 不具事务性，失败后核对实际 schema 再处理。新库运行更新后的 `alter_customer_service_knowledge.sql`。
4. 同步部署 Java/Python v2；旧 FAILED 任务只留审计，不手工重置为待执行。需要重做的任务从新版业务入口产生。先对每个需要提供检索的文档调用新版 reindex，由业务事务递增 indexRevision 并执行 v2 INDEX；禁用文档需明确选择 enable，不能用 reindex 绕过。逐文档确认 INDEX 成功、Spring serving 身份落账及 v2 manifest/块验收，全部完成后才允许执行全库 rebuild 并恢复流量。不能首次直接用 SQL 回填的旧数字身份执行 rebuild，也不能把旧 manifest 伪装成同数字 indexRevision 的 v2 manifest。
5. v2 rebuild 先重新下载、校验原文并解析分块，再于存储层同一变更许可内核对旧 alias 已验收的同一 v2 manifest、完整块内容/locator/hash及模型/维度，复用其完整原向量；写入及发布前仍逐值复核，不向供应商重新计算该索引版本的向量。内容不一致返回 `KNOWLEDGE_VERSION_CONFLICT`，缺对应 manifest/完整块返回 `KNOWLEDGE_REBUILD_REVISION_UNVERIFIABLE`，不回退重新Embedding。旧 alias 为空或丢失原 manifest 时，应先通过新代际 INDEX 建立可核对基线，INDEX 可以 bootstrap；不能把该路径称为有证明的灾备恢复。解析/分块变化或需要重算向量需新代际 INDEX；向量模型签名变化不能直接 INDEX 到旧模型集合，本阶段尚无新版模型的独立候选发布流程，需后续迁移设计，不能靠改配置强行重建。协议升级标记独立于 alias 持久化，不能通过 legacy rebuild 或回滚集合降级写入。旧协议重建保留原Embedding流程。
6. 检查 serving 的 registry 关联、索引修订和激活修订，验证替换失败重建、禁用后重新启用、迟到删除以及重试身份。已有 alias 的空候选 rebuild 一律返回 `KNOWLEDGE_EMPTY_REBUILD_BLOCKED`，不能将其作为清空知识库指令。

B0a 只提供版本身份和不可变原文基础。Spring 权威发布指针与 alias outbox 属于 B0b，尚未实现；当前租约前后校验并不构成跨 MySQL/Milvus 的原子发布，不能声称整个 B0 已完成。

## 显式资源测试与运维入口

`KnowledgeRevisionMysqlPreflightTest` 只读本地 Spring datasource，需 `AI_KNOWLEDGE_MYSQL_PREFLIGHT=true`；报告仅含版本身份、聚合计数和稳定任务错误码。`KnowledgeRevisionMysqlMigrationTest` 需 `AI_KNOWLEDGE_MYSQL_MIGRATION_INTEGRATION=true`，仅创建/清理随机前缀隔离 schema，不改业务表；会验证备份 SQL 往返及迁移阻断条件。

`KnowledgeRevisionMysqlApplyTest` 是一次性实际业务迁移入口，只有 `AI_KNOWLEDGE_MYSQL_APPLY_V2=true` 才执行；必须先按上文停写、排空。它备份四张知识表（包含对象引用及租约数据，文件需私下保存），再次核对前置条件，再执行受控仓库 SQL；拒绝已存在 v2 列/registry 的库，失败不自动重试或回滚。测试默认跳过并不表示业务迁移已经成功。

Python `tests/test_knowledge_revision_milvus_integration.py` 需 `AI_SERVICE_MILVUS_INTEGRATION=true`，使用固定本地 Milvus 地址、随机 alias、合成向量和进程内协调器，精确清理本例资源；不会调用真实 Embedding，也不代表 Spring 多实例 fence 验收。

### 任务回执和所有权边界

DELETE/REBUILD 的Python调用成功后，worker必须由当前owner条件更新任务终态且命中1行才记录成功。命中0行视为失去claim，旧执行不再重写失败或重试；Mapper异常只记录落账结果未知，不能断言数据库没有提交或Milvus没有执行。没有自动对账能力，仍需核对持久任务和索引事实；原PROCESSING任务到期可能被认领，不保证副作用严格一次。

`KnowledgeOwnershipMysqlIntegrationTest` 需 `AI_KNOWLEDGE_MYSQL_OWNERSHIP_INTEGRATION=true`，在随机隔离schema使用真实生产Mapper及Spring事务代理，验证双物理连接认领、过期接管、旧owner成功/失败的事务回滚、版本推进与Coordinator互斥。它模拟持久PROCESSING的丢回执状态，不制造真实网络丢包，也不代表完整双Spring/Python/Milvus实例验收。逐轮结果及未完成的B0b见 `customer-service-rag-rounds-handoff.md`。

2026-11-16 本机已执行备份、SQL迁移和11篇新INDEX，确认72活动块。首次采用重算向量的整库重建失败，alias未切换；用户随后确认采用原向量复用。实际部署及重建结果详见 `customer-service-rag-evaluation-handoff-20261116.md` 真实资源续验。
