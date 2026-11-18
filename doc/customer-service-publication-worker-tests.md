# 客服知识发布 worker 并发与提交确认测试

本文件描述工作包 A 的测试设施与覆盖边界。生产发布协议和业务行为未改动。

## 运行入口

在仓库根目录执行；MySQL 必须使用已有本地配置，账户需能创建及删除随机 schema。

```powershell
$env:AI_KNOWLEDGE_PUBLICATION_MYSQL_INTEGRATION = 'true'
mvn '-Dtest=KnowledgePublicationWorkerMysqlIntegrationTest' test
```

默认不调用模型或 Milvus；不开资源开关时整类跳过。要运行同一 A1 时序的真实 Milvus 复验，还需本地 `localhost:19530`、已建立的 Python 3.12 `.venv` 和以下开关：

```powershell
$env:AI_SERVICE_MILVUS_INTEGRATION = 'true'
mvn '-Dtest=KnowledgePublicationWorkerMysqlIntegrationTest' test
```

Python 环境不存在时，先在 `ai-service` 执行 `uv sync --frozen --python 3.12`。测试直接启动该环境的 Python，使用 `-X utf8` 传输中文收据。Windows 的该入口本身仍是启动器：等待期间持续登记实际后代，超时时对本例精确根 PID 执行 `taskkill /T /F`，再按已观察句柄补终止并有界确认整树退出；未确认时禁止清理 Milvus。`KnowledgePublicationProcessCleanupTest` 不连接资源，实际创建解释器及休眠后代验证这条路径，不按进程名全局终止；没有本地虚拟环境时明确跳过该探针，Java 常规测试不会因此新增强制 Python 安装要求。

## 真实边界与断言

- A1：真实 worker A 对文档 D/V1 准备候选，在真实发布事务代理外以 reached/release 两个有界 latch 暂停，明确断言未持有事务。共享可控时钟推进 31 秒，使 A 的 30 秒租约自然过期、120 秒 claim 仍有效。真实管理 `upload(..., replacementId=1)` 产生 V2 及 B 任务，B 经正常 `poll/claim/acquire/prepare/commit` 成功发布。没有为争抢租约 SQL 修改任务、owner 或租约状态。
- B 发布后的前置条件明确包含任务 `SUCCEEDED`、无失败重试，以及 D 的 V2 内容哈希/对象键、`indexedVersion=2`、`indexedEtlVersion=3`、激活修订和 `ACTIVE` 状态。随后从独立数据库连接保存完整 state/document/content_revision/publication/alias_outbox 与 B task/anchor/execution 各列。A 恢复并实际运行 worker 异常和 finally 后再次独立读取并逐列相等；A 必须明确收到 `KNOWLEDGE_PUBLICATION_LEASE_INVALID`，不得产生 publication/alias outbox。
- A1 替代租约：B 已发布后另保留一个正常签发、当前有效的同文档替代 lease。A 的真实 finally 确实执行旧 lease 撤销，替代 lease 仍有效。
- 独立 epoch 冲突：原 claim、lease、文档身份均仍有效，仅推进管理 epoch 后，旧候选明确报 `KNOWLEDGE_PUBLICATION_EPOCH_CONFLICT`，完整状态不变。
- A2：A 的真实事务 Mapper 获得权威 state 行锁后暂停，B 执行真实管理替换。通过 MySQL `performance_schema.data_lock_waits` 或兼容 InnoDB 锁等待视图核对 blocker/waiter 连接 ID 和本例 state 表，证实实际等待。先放 A，随后 B 完成；发布事实为 epoch 2，管理变更为 epoch 3。若两种视图均不可读，明确跳过本例，不能据 Future 未完成宣称覆盖。
- A3：包装 commit，先调用真实事务代理并完成数据库提交，再抛连接类异常。匹配不可变收据时确认任务成功，不执行 fail/retry，也不再次 prepare/publication/推进 epoch。另一例提交成功后事实查询不可用，仍保持数据库既有 `SUCCEEDED`。补充的未提交且确认查询不可用例保留 `PROCESSING`，不写失败或自动恢复。
- 真实 Milvus：使用相同 A1 worker/管理/MySQL 发布时序，候选边界以合成向量调用生产 `PublicationStore.write`。随机 alias 下保存 bootstrap、A 和 B 的真实私有集合；B 发布后及 A 收尾后均用从真实 MySQL 返回的同一 B 快照检查集合/发布 ID/摘要、完整收据和 V2 向量命中。alias 仍指向 B。

## 夹具与证据

复用 `KnowledgePublicationMysqlIntegrationTest` 的随机 `rag_publication_it_<uuid>` schema 和真实 MySQL Mapper/事务代理。为真实管理上传补齐 FlexDataSource/FlexConfiguration/实体注解雪花 ID 注册；每 schema 在 Mapper 注册前建立独立 FlexGlobalConfig，并在退出时移除，避免首例依赖前例静态残留。生成器类型有显式断言。

每个场景将原始状态与锁证据写入被忽略的 `tmp/rag-publication-worker-*`；命令与本轮执行报告保存在 `tmp/rag-work-package-a-20261118/`。MySQL schema 由夹具 finally 删除；Milvus 在创建 RPC 前登记精确集合名，清理时再次核验随机 aliasHash 和 publication kind，不扫描/删除业务集合。

## 限制

这里不调用真实供应商、OSS 下载或 Embedding。Milvus 候选侧使用本例进程许可及合成向量，Spring 的 owner/claim/lease/epoch 校验由真实 Java/MySQL 事务承担；这不是完整 Spring/Python HTTP 故障演练。未提供自动接管、自动恢复、长期暂停、全部并发组合或未知提交的通用恢复证明。

首次调试中的 Flex 数据源/配置注册、反射泛型重载、JavaTime 证据序列化，以及 Windows 子进程输出编码问题均是测试设施问题，原始失败报告保留，不能以最终通过覆盖这些历史记录。
