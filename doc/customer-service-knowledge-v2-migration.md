# 客服知识 v2 版本基础（B0a）迁移

`documentVersion` 是内容修订，`etlVersion` 是每文档稳定索引修订；同一 outbox 的重试复用后者。每次重建任务的顶层 `etlVersion` 仅代表 buildRevision，逐文档索引身份来自 serving 字段。UUID `operationId` 只代表单次执行租约。

新文档 `activationRevision=1`；禁用后的启用、禁用文档替换上传递增激活。禁用/删除以当前激活为墓碑上界，Python v2 删除该文档所有激活不大于该值的索引。DISABLED 不能通过 reindex 绕过启用。普通替换/重新索引保持激活，失败保留旧 serving 三元组。成功落账在同一条件 UPDATE 更新 indexedVersion、indexedEtlVersion、indexedActivationRevision。

重建查询 ACTIVE/UPLOADED/INDEXING/FAILED 中属于当前激活的 serving，按不可变原文登记读取已索引内容。LEFT JOIN 缺原文或版本字段非法时明确失败，不能静默遗漏。旧 OSS 对象不得由清理任务删除。原文注册不等于长期保留策略已实现。

## 执行顺序

1. 备份业务库和 OSS 原文，关闭知识上传、启用、禁用、重索引和重建入口；停止自动领取的新任务，等待所有旧 PENDING/PROCESSING 完成，再停止 worker。超时任务先按现有恢复流程处置，不能更换账本绕过。
2. 执行迁移前 SELECT。旧库 `indexedVersion != documentVersion` 无法从当前 objectKey 推导旧原文；即使相等，非 ACTIVE 的 serving 索引修订也不可凭当前 etlVersion 伪造。必须先在旧版恢复历史原文及索引身份，或经运维明确选择原文重新索引/停用；脚本 SIGNAL 阻断，不自动丢弃旧 serving。没有历史原文时无法承诺恢复旧内容。
3. 在支持遇错停止的 MySQL 客户端执行 `sql/alter_customer_service_knowledge_v2_revisions.sql`。该脚本仅用于未加 v2 列的旧库一次性迁移；DDL 不具事务性，失败后核对实际 schema 再处理。新库运行更新后的 `alter_customer_service_knowledge.sql`。
4. 同步部署 Java/Python v2；旧 FAILED 任务只留审计，不手工重置为待执行。需要重做的任务从新版业务入口产生。先对每个需要提供检索的文档调用新版 reindex，由业务事务递增 indexRevision 并执行 v2 INDEX；禁用文档需明确选择 enable，不能用 reindex 绕过。逐文档确认 INDEX 成功、Spring serving 身份落账及 v2 manifest/块验收，全部完成后才允许执行全库 rebuild 并恢复流量。不能首次直接用 SQL 回填的旧数字身份执行 rebuild，也不能把旧 manifest 伪装成同数字 indexRevision 的 v2 manifest。
5. v2 rebuild 必须逐块复现旧 alias 已验收的同一 v2 身份，包括内容、locator 和向量；不一致返回 `KNOWLEDGE_VERSION_CONFLICT`，没有对应 v2 manifest 返回 `KNOWLEDGE_REBUILD_REVISION_UNVERIFIABLE`。旧 alias 为空或丢失原 manifest 时，应先通过新代际 INDEX 建立可核对基线，INDEX 可以 bootstrap；不能把该路径称为有证明的灾备恢复。解析/分块变化需新代际 INDEX；向量模型签名变化不能直接 INDEX 到旧模型集合，本阶段尚无新版模型的独立候选发布流程，需后续迁移设计，不能靠改配置强行重建。协议升级标记独立于 alias 持久化，不能通过 legacy rebuild 或回滚集合降级写入。
6. 检查 serving 的 registry 关联、索引修订和激活修订，验证替换失败重建、禁用后重新启用、迟到删除以及重试身份。已有 alias 的空候选 rebuild 一律返回 `KNOWLEDGE_EMPTY_REBUILD_BLOCKED`，不能将其作为清空知识库指令。

B0a 只提供版本身份和不可变原文基础。Spring 权威发布指针与 alias outbox 属于 B0b，尚未实现；当前租约前后校验并不构成跨 MySQL/Milvus 的原子发布，不能声称整个 B0 已完成。
