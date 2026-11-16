# 客服 RAG 逐轮优化交接

更新日期：2026-11-16。此文是后续每轮的入口，历史详证保留在 `customer-service-rag-evaluation-handoff-20261116.md`。每轮结束追加记录，不覆盖此前失败或把计划写成已实现；必须同时在交付文字中说明改动、原因、流程、边界、验证及下一步。

## 当前已确认基线

- 后端仓库 `D:/VibeForge/yu-ai-code-mother`，前端独立仓库本轮未修改。
- Spring8123、Python8000、Redis、MySQL和Milvus/PostgreSQL依赖已恢复；只描述2026-11-16本次检查，不保证后续机器重启仍运行。
- 11篇公开文档、72活动块、11份v2 manifest；旧禁用文档仍禁用。内容/激活版本1，索引版本首篇5、其余3。
- 生产保留R1、dense Top8、每文档1块、final3。R2、cap2/3、Top16/24均未推广。
- 代码提交：`536b635`（版本身份）、`786d230`（证据及拒答评测）、`8862024`（原向量复用与真实迁移）。用户既有README格式修改及无关未跟踪计划保留。

## 第一轮：版本基础、评测与真实索引恢复（已完成的范围）

### 改了什么、为什么

旧实现把内容、ETL执行、激活和全库构建混用：同内容重索引发生manifest冲突；禁用后启用被旧墓碑挡住；全库build身份污染单篇索引；替换失败后旧serving原文无法可靠重建。先修版本正确性，再讨论回答质量。

同时按系统评估流程将上下文相关性、事实支持、答案相关性、拒答、版本安全、延迟/调用分别测量；保留逐题结果和调用预占，不能用answered=true或抽样裁判高分代替质量证明。

### 如何实现

Spring以documentVersion记录不可变内容，以每文档etlVersion记录稳定indexRevision，以activationRevision记录启用资格；成功后保存serving三元组。内容registry登记每个内容版本的原始OSS对象。Python块/manifest绑定三种身份，控制集合持久化激活墓碑与协议标记；低代激活的迟到删除不能撤销新激活。

真实执行先停worker、排空并备份四表，再一次性迁移，逐篇通过新版管理员API产生11次新INDEX，逐一等待成功并核对三元组，最后执行整库重建。首次严格重算重建失败且alias保持不变；用户确认后改为v2重新下载、核hash、解析、分块，再于同一变更许可中核对旧完整快照并复制原向量，保留提交前逐值复核。向量重算必须走新INDEX代际，旧协议仍沿原Embedding路径。

关键模块：`CustomerServiceKnowledgeServiceImpl`、`CustomerServiceKnowledgeEtlWorker`、`CustomerServiceKnowledgeTaskFinalizer`、原文registry Mapper、`document_etl.py`、`milvus_knowledge.py`及显式资源夹具。

### 失败和边界如何处理

- 缺旧manifest/完整块不回退重算，返回不可验证；正文、定位、hash或身份改变返回冲突。坏向量、模型或维度错配拒绝。
- 已有alias的空重建拒绝；新协议标记不随alias回退而消失；墓碑不清除。
- 重建失败保留旧alias，失败候选清理；历史FAILED任务保持审计，不重置账本绕过预算。
- 第一次真实REBUILD冲突的当次候选向量未保存，精确根因没有事后证实。后续首篇重算一致不证明失败当次一致；成功采用快照复用也不反向证明原供应商发生漂移。
- 快照复用要求完整源集合，不是向量丢失后的灾备保证；模型升级和分块变化仍需新代际及独立迁移。
- 当前MySQL/Milvus仍没有原子发布；租约前后检查无法阻止任意检查后暂停再写。B0b权威指针、publication账本、epoch提交和alias outbox尚未实现。

### 证据与验证

- Python最终1531 passed/20 skipped/2 warnings；compileall与uv lock通过。20项跳过为14 PostgreSQL、6显式Milvus。
- 真实隔离Milvus6 passed，含复用前后向量exact一致；真实隔离MySQL迁移4 passed；Java相关总计71 passed、2运维入口跳过；干净编译通过。
- 实际业务库迁移成功，备份hash及路径见真实交接文档；11次INDEX全部成功。首次REBUILD失败保留alias，随后批准的REBUILD `479832495083827200` 一次成功。
- alias最终为 `customer_service_knowledge_9a3e5b421e0e_staging_446409c019dc`；72块校验字段及f32向量与原集合逐值一致、Spring serving身份匹配，最终无待执行/处理中任务。
- 两条真实query Embedding+Milvus Top8烟测2/2命中目标文档；未新增回答模型调用，不代表完整问答验收。
- 证据选择cap2比cap1的最终必要事实宏覆盖90.2083%→93.1250%，cap3无新增收益；扩大TopK未改善最终覆盖。
- 16题拒答配对、R1/R2各三轮共96真实回答：拒答21/24→24/24，公开题两侧24/24无误拒。R2有公开016支持范围疑义，保持候选。112项人工校准队列待裁决；冻结评测gold未改。

### 下一轮及原因

先补B0b前置的真实多连接接管/迟到回执/事务回滚验证，并修复有证据的任务落账问题。它能明确“存储副作用已发生、任务是否有权成功”的差别，为权威publication协议提供可复现的故障模型。然后实施B0b完整协议，最后再推进证据组合/标题向量和人工校准；不把所有内容捆绑成一次生产变更。

## 第二轮：并发所有权与回执落账（已完成并部署）

### 问题与目标

源码初查：INDEX的文档与任务终态已由REQUIRES_NEW事务及owner条件写保护，实际回滚尚需真实多连接证明；DELETE/REBUILD完成后直接调用finish而未检查返回行数，可能在owner被接管或任务已结束时仍记录“成功”。这不是已证明的向量污染，但会造成成功观测与持久任务状态不符。过期claim可被另一实例认领，不代表旧执行或RPC副作用已经停止。

### 处理方案与取舍

沿用已批准路线B的版本正确性范围，本轮选择有界并发验收及条件落账：使用随机schema、独立连接/事务和两个不同owner，验证认领竞争、过期接管、旧owner终态拒绝、INDEX文档与outbox事务回滚，以及禁用/新激活后迟到INDEX不可覆盖serving。worker必须检查非INDEX终态CAS，失去claim不得报告成功或重写新owner任务；落账异常作为状态未确认记录，不当成执行从未发生。

仅增加进程锁不能解决跨实例所有权；直接宣称引入权威publication也不成立。本轮不更改现有发布协议、不清空生产数据、不调用收费模型；真实测试只触及自身随机schema。完整B0b仍需后续独立的提交协议与迁移设计。

### 边界、失败与验证要求

- 以同步屏障/确定性SQL而非随意sleep构造竞争；每次测试finally精确清理本次schema。
- 旧owner失败或晚到成功不得改变替代owner；事务中outbox CAS失败必须回滚同事务文档变化。
- 数据库异常、终态CAS为0与Python实际操作失败分开，不能把没有持久成功当成Milvus没写入。
- 普通离线测试与真实MySQL测试分别记录；两个事务客户端不冒充两套完整Spring/Python/Milvus实例。
- 验证完保留当前在线索引；不以本轮通过声称解决检查后暂停/alias双写/在途禁用读可见性。

### 实际改动及边界处理

`CustomerServiceKnowledgeEtlWorker.completeNonIndexTask` 专门处理DELETE/REBUILD的完成回执。Python成功后只有outbox.finish命中1行才继续记录成功；命中0行进入LostClaim分支，不再改失败或重试。Mapper异常进入TaskFinalizationException专门分支，固定诊断为`KNOWLEDGE_TASK_FINALIZATION_FAILED`，日志只记录任务身份、操作、固定码和异常类型。INDEX原诊断码保留。

落账异常改用“finalization outcome unknown”：数据库可能未写入，也可能已经提交但确认丢失；不声称一定未提交、一定可重新认领或Milvus未执行。当前没有自动publication对账，持久PROCESSING仍可能按既有超时机制被接管，副作用仍非严格一次。finally继续只按本执行operationId/fence撤销自己的lease，不能撤销替代执行。

### 本轮验证与运行状态

- 红测：新增DELETE/REBUILD各3种回执场景，原实现30项中4项失败（两操作×CAS0/数据库异常）；修复后worker30/30通过。
- 最终Java命令：显式`AI_KNOWLEDGE_MYSQL_OWNERSHIP_INTEGRATION=true`，执行 `mvn test '-Dtest=CustomerService*Test,InternalCustomerServiceKnowledgeControllerTest,KnowledgeMutationCoordinatorTest,KnowledgeOwnershipMysqlIntegrationTest' -q`，**106 passed、0 failed/error/skipped**，其中99项普通测试、7项真实MySQL。`mvn clean -DskipTests compile -q`通过。
- 真实7项使用生产Mapper、Finalizer/Coordinator及Spring事务代理，包含不同物理连接同时认领、固定过期边界、旧owner成功/失败落账导致文档与任务整笔回滚、当前owner双写成功、5种禁用/删除/内容/ETL/enable式激活推进保护、4类租约范围竞争及过期/撤销proof。最终7个随机schema均DROP后查询确认不存在。
- 两位只读审阅分别完成规格/代码质量核对；没有将审阅当作测试执行。普通及真实结果见本机忽略日志`tmp/round2-worker-red.log`、`tmp/round2-final-tests.log`。
- 业务库只读预检0 PENDING/PROCESSING后，重启本轮已授权管理的Spring使修复生效，运行参数沿前轮，不改模型/索引配置。管理员健康、Python checkpoint及RAG依赖全通过；运行记录`tmp/round2-runtime-verification.json`。
- 生产collection仍为`customer_service_knowledge_9a3e5b421e0e_staging_446409c019dc`；11 ACTIVE/1 DISABLED、72活动块、11份manifest和Spring serving三元组均匹配。没有触发新INDEX/REBUILD、真实回答或Embedding，没有迁移业务schema。
- 本轮未改Python代码，未为Java回执修复重复Python全量；1531/20的Python结果仅属于前一轮，不能称本轮重跑通过。完整双Spring/Python/Milvus故障注入、真实网络丢包、数据库提交确认丢失及跨资源原子发布仍未测。
- 源码/测试提交：`02b88e5`；交接入口首次提交`30dc059`、执行计划`f9b3c30`。本轮生产修复范围仅上述回执分支，不声称整个B0b已完成。

### 下一轮说明

下一轮推荐下节B0b权威发布协议。原因是本轮只确保数据库条件落账和成功日志可信，无法消除“最后检查后暂停、旧执行恢复再写Milvus”窗口。需要将权威指针、稳定publication事实、私有候选、读路径及alias对账一并设计，不能只给现有alias再加一把锁。

## 第三轮：B0b 权威发布协议（实施中，尚未切换）

### 为什么需要、什么情况下触发

第二轮只保护MySQL终态及成功观测；Python在最后一次租约检查后暂停、旧执行随后恢复并写Milvus/切alias的窗口依然存在。MySQL成功指针、Milvus活动标记及alias可能不一致。下一轮应在发布和读取两侧建立一个权威版本，而不是继续增加检查次数。

### 推荐流程与关键模块

1. Spring维护知识库epoch、稳定publicationId、不可变publication事实和活动指针；任务重试复用publicationId，attempt/租约另计。改变应使旧构建失效的管理员事务同步推进epoch。
2. Python构建私有不可变候选，写入后验证文档/索引/激活清单、模型/维度、块集合与内容指纹；准备阶段不得改变可服务集合。只重算需要新INDEX的内容，同代重建复用已验证向量。
3. Spring worker通过带认证的内部API要求Python准备候选收据，再调用Spring本地事务Service提交；Python不直接访问业务MySQL。提交锁定epoch、核对task owner/claim、fence、禁用及serving快照，登记publication并切权威指针。快照或owner过期则拒绝候选，不激活。
4. HTTP提交响应丢失时按稳定publicationId查询事实：已提交返回原结果，未确认保持未知；不同指纹不可覆盖同ID，也不能更换ID绕过。
5. 同事务生成alias对账outbox。alias仅作兼容镜像，查询必须使用Spring权威目标的具体collection；否则迟到alias写仍可影响检索。对账失败不撤销已提交事实，延后按当前权威目标修复。
6. 读路径定义快照语义、禁用生效点和有界epoch复核。需要明确在途答案是否允许旧快照；最后一次复核后仍可能发生撤销，不能宣传绝对即时撤销。本轮已确认模型调用前后复核epoch，变化丢弃结果且不自动付费重试；最后复核后仍存在变更窗口。

涉及Spring知识管理Service、worker/finalizer、publication/epoch/outbox实体和Mapper、新迁移SQL、内部提交/查询API；Python知识存储与查询、ETL提交适配、schema和契约测试。候选组织已按72块实测和用户确认采用有界整库复制；不增加按文档查询扇出，超过预算明确失败，未来规模变化另行评估。

### 收益、代价与仍可能失败的情况

收益是把“已构建”与“已发布”分开，允许重复提交查询、阻断旧owner提交，并使alias失配不再成为读取权威。代价是增加数据库/内部HTTP依赖、候选存储、迁移和对账维护；只加分布式锁改动小，但无法抵御旧进程检查后暂停恢复，因此不推荐作为完整替代。

数据库不可用时读取/提交应明确失败关闭，不能回退到未经权威核对的旧alias。候选丢失/不完整时不推进指针；提交已成功但候选随后丢失需要显式运维恢复，不能自动伪造新事实。失败候选及旧release不自动清理到只剩一个版本；回收需另做权威引用保护。迁移需停写排空、备份、验证旧serving、统一升级读写实例，禁止混跑旧alias权威实例；启用新权威后不能仅回退旧代码或切旧alias。

### 预定验收

双实例及真实MySQL/Milvus隔离数据覆盖：检查后暂停/旧owner恢复、任务接管、禁用/启用与重建交错、同publication重试/指纹冲突、提交前后进程退出、数据库提交响应丢失、alias写失败/迟到覆盖/对账收敛、权威目标缺失和空候选。逐项核对读到的文档/索引/激活身份；本机维护迁移不能当作完整多实例生产推广证据；完整进程崩溃与网络丢包矩阵需另行验证。质量实验另行进行，不用版本测试通过替代答案事实正确率。

### 本轮已完成的组件与当前边界（实施中记录）

用户已明确批准“有上限的整库不可变候选 + 回答生成前后 epoch 复核”。本机旧源测量为72块、11份manifest、83行，完整向量JSON约4.59MB；采用整库候选可保持单集合检索，代价是每次发布复制快照。详细已确认协议见 `docs/superpowers/specs/2026-11-16-rag-authoritative-publication-design.md`，维护流程见 `doc/customer-service-knowledge-publication-migration.md`。

- Spring短事务已经实现epoch→task→lease锁序，同事务落publication、head、serving、任务成功和alias outbox；管理变更先锁state并推进epoch。读控制快照持同一state锁获取head/名单，不跨时点拼接。
- worker在新候选准备前及提交确认未知时查询稳定task publication事实。查询仍不确定则保留未知，不将已发生副作用改写成普通失败；相同收据的事实确认不要求原claim/lease仍有效。
- 维护bootstrap、带认证的snapshot/fact入口和周期head alias对账已实现；即使已ACK仍复核镜像，维护期无head不发送空目标。具体别名没有CAS，迟到覆盖仍可能短暂发生；权威读链路接入后将不依赖镜像。
- Python私有候选、严格收据、源清单/f32向量校验、完成标记与原文重解析已实现。INDEX仅为目标新索引计算Embedding；REBUILD重解析后复用原向量；DELETE最后一篇允许完整空快照，空REBUILD仍阻断。失败候选保留，旧写路径拒绝publication具体target。
- 审查发现并修复REBUILD取租约前后清单漂移，以及query iterator创建期间取消遗漏关闭、关闭异常覆盖主取消/错误。六项确定性红绿测试覆盖iterator生命周期，不将取消当作可发布完成。
- Spring响应仍默认64KiB，Python在Embedding前保守预检收据；候选新增64MiB实际JSON上限。已知累计块/文本/向量预算在Embedding前核对；实际向量JSON超限可能在收费之后发生，不能宣传任何失败均零费用。
- 普通读取接口只获取完成标记和命中证据标量，不每题下载整库向量；候选构建、源复制及显式完整验收仍逐值回读。不可变应用写边界不等于任意运维绕过协议后的全库篡改检测。

本阶段root独立验证：Java最终142项中141通过、1运维入口跳过，含14项真实隔离MySQL；干净编译通过。Python候选相关187项通过，含2项真实随机alias Milvus，逐项清理确认；相关日志为 `tmp/b0b-root-java-final-tests.log`、`tmp/b0b-root-java-final-test-summary.json`、`tmp/b0b-root-java-final-compile.log`、`tmp/b0b-root-candidate-final-tests.log`。规格与质量均经过只读审查，审查不冒充测试执行。两次Maven曾争用共享target导致审阅尝试缺类，随后root独占clean重跑通过，没有为缓存问题改生产代码。

当前已提交组件：`35153ff`（Java权威事务）、`06b25cc`（Python私有候选及取消边界）。目前正在实施HTTP权威客户端、read scope、回答前后epoch复核及app组装；完整Python回归、业务库维护迁移/备份、真实bootstrap和在线切换尚未完成。当前线上旧索引未切换，不把上述组件完成写成整个B0b已部署。下一阶段必须先完成剩余读写闭环和验收，再执行维护切换。

## 以后每轮必须使用的交接格式

1. 本轮解决什么：原问题的具体触发场景、证据和目标。
2. 为什么选此方案：收益、代价、未选方案及不适用条件。
3. 怎么做：触发条件、判断依据、处理流程、关键模块与协议/配置变化。
4. 边界与故障：并发、重试、取消、旧版本、丢响应、资源不可用如何处理；哪些保证仍不存在。
5. 验证证据：离线/真实分开，命令、计数、失败及修复、成本/预算与未测项；禁止沿用历史结果冒充本轮。
6. 交付状态：提交哈希、已部署范围、当前资源与回退限制，保留无关用户修改。
7. 下一轮：问题/原因/实现路径/边界/预定验收；每轮只推进可归因变量，不自动升级质量候选。
