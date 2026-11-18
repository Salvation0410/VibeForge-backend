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

## 第三轮：B0b 权威发布协议（已完成本机维护切换）

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

### 组件实施过程与边界记录

用户已明确批准“有上限的整库不可变候选 + 回答生成前后 epoch 复核”。本机旧源测量为72块、11份manifest、83行，完整向量JSON约4.59MB；采用整库候选可保持单集合检索，代价是每次发布复制快照。详细已确认协议见 `docs/superpowers/specs/2026-11-16-rag-authoritative-publication-design.md`，维护流程见 `doc/customer-service-knowledge-publication-migration.md`。

- Spring短事务已经实现epoch→task→lease锁序，同事务落publication、head、serving、任务成功和alias outbox；管理变更先锁state并推进epoch。读控制快照持同一state锁获取head/名单，不跨时点拼接。
- worker在新候选准备前及提交确认未知时查询稳定task publication事实。查询仍不确定则保留未知，不将已发生副作用改写成普通失败；相同收据的事实确认不要求原claim/lease仍有效。
- 维护bootstrap、带认证的snapshot/fact入口和周期head alias对账已实现；即使已ACK仍复核镜像，维护期无head不发送空目标。具体别名没有CAS，迟到覆盖仍可能短暂发生；权威读链路接入后将不依赖镜像。
- Python私有候选、严格收据、源清单/f32向量校验、完成标记与原文重解析已实现。INDEX仅为目标新索引计算Embedding；REBUILD重解析后复用原向量；DELETE最后一篇允许完整空快照，空REBUILD仍阻断。失败候选保留，旧写路径拒绝publication具体target。
- 审查发现并修复REBUILD取租约前后清单漂移，以及query iterator创建期间取消遗漏关闭、关闭异常覆盖主取消/错误。六项确定性红绿测试覆盖iterator生命周期，不将取消当作可发布完成。
- Spring响应仍默认64KiB，Python在Embedding前保守预检收据；候选新增64MiB实际JSON上限。已知累计块/文本/向量预算在Embedding前核对；实际向量JSON超限可能在收费之后发生，不能宣传任何失败均零费用。
- 普通读取接口只获取完成标记和命中证据标量，不每题下载整库向量；候选构建、源复制及显式完整验收仍逐值回读。不可变应用写边界不等于任意运维绕过协议后的全库篡改检测。

本阶段root独立验证：Java最终142项中141通过、1运维入口跳过，含14项真实隔离MySQL；干净编译通过。Python候选相关187项通过，含2项真实随机alias Milvus，逐项清理确认；相关日志为 `tmp/b0b-root-java-final-tests.log`、`tmp/b0b-root-java-final-test-summary.json`、`tmp/b0b-root-java-final-compile.log`、`tmp/b0b-root-candidate-final-tests.log`。规格与质量均经过只读审查，审查不冒充测试执行。两次Maven曾争用共享target导致审阅尝试缺类，随后root独占clean重跑通过，没有为缓存问题改生产代码。

上述为组件阶段记录；后续完成HTTP/read scope/app集成与本机维护切换，实际结果如下。

### 本轮最终实现、验证与部署（2026-11-16）

权威HTTP客户端、`AuthoritativeKnowledgeStore`、候选入口和app装配已提交 `ceaa943`；前置实现为 `35153ff`（Java事务）、`06b25cc`（私有候选）。每请求用独立ContextVar保存控制快照，检索只查权威具体集合和文档身份白名单，回答模型前后都复核完整快照。变化或数据库/API故障返回无答案、无引用，不进行付费重试；空名单跳过query Embedding和回答模型。准备接口严格绑定当前epoch/source/预期名单，旧INDEX/DELETE/REBUILD逐请求检查持久模式，禁止关本地开关绕回旧协议。

两轮只读审查中发现HTTP依赖注入可以传入未绑定权威store的官方回答服务，现已拒绝裸官方服务和未知实现。测试兼容器必须显式使用LegacyGuard，且不能包装官方RAG或用于publication模式；未知兼容callback仅保证整段调用前后检查，不承诺其内部模型前hook。启动时尚未bootstrap或控制面临时故障只影响知识健康，不销毁候选准备依赖，避免首次迁移无法自举。

- root独立执行 `uv run python -m compileall -q src`、`uv run pytest -q`、`uv lock --check`：**1644项收集，1622通过、22跳过、0失败/错误**。常规完整回归未开启真实资源opt-in；前述187项另含显式隔离Milvus的2项。日志与计数在本机忽略路径 `tmp/b0b-root-python-full-tests.log`、`tmp/b0b-root-python-full-summary.json`。测试前停止自有Python以释放GPU owner锁，完成后用离线模型缓存重启。工作区同时有另一任务的Vue提示改动，未纳入本轮提交；完整测试覆盖当时工作区，不能据此将另一任务改动归因给本轮。
- Java独立验证仍为本轮前述141通过、1维护入口默认跳过，含14项隔离MySQL；干净编译通过。停Spring后显式运行 `AI_KNOWLEDGE_MYSQL_APPLY_PUBLICATION=true` 的 `KnowledgePublicationMysqlApplyTest`，该1项真实业务维护入口通过。先确认全局0 PENDING/PROCESSING及新三表不存在，在一致快照中备份五表，再迁移并核对旧五表结构/全部字段指纹不变。
- 迁移备份为本机忽略目录 `tmp/knowledge-publication-backup-d9bf7b6e-3655-414d-9e85-6584079ccd79/knowledge.sql`；SHA-256为 `bb9b127ff97210da5f7eb9026c1ae9e640cd45b22e38117a041c305c46532b12`。不可将含业务数据的备份提交Git。
- 新版两端维护启动后，管理员bootstrap真实下载并解析原文，复制已验收向量，成功提交 `bootstrap_8fae81e298d34db990405fa2883eb64e`，**epoch=1**。权威集合为 `customer_service_knowledge_pub_a5a7821339274e29a82f241ffaaad8e5`，snapshotHash为 `a39a17a7cd024f447283a2773c471c786851a4674cb65f804d596b9e7699e682`。
- 完整回读新83行（72块/11 manifest），排除旧可变控制字段后的原业务字段及规范f32向量逐项一致；规范SHA-256仍为 `a4381d141c7648203b85d60d10a0ebc4ed436acafa801556deeddc0db4ca0ad7`。旧具体集合 `customer_service_knowledge_9a3e5b421e0e_staging_446409c019dc` 保留且哈希未变。管理员状态仍11 ACTIVE、1 DISABLED。
- 使用保存的原块向量执行真实Top8具体集合检索，命中身份均在白名单；检查snapshot/fact整数JSON、错误token拒绝、alias镜像与权威head一致、两端健康。上述迁移/回读验收 **回答模型0次、Embedding 0次**，不能将保存向量检索视为真实自然语言问答质量评估。
- 精确备份并追加私有本地配置：Spring publication-enabled=true、bootstrap-allowed=false；Python publication=true。关闭维护入口后重启Spring并复验，普通重启继续新协议，不提交私有配置或密钥。已启用后不能仅关闭开关或切旧alias回滚。运行证据为 `tmp/b0b-runtime-verification.json`、`tmp/b0b-bootstrap-result.json`、`tmp/b0b-maintenance-closed.json`、`tmp/b0b-admin-preflight.json`。

本轮边界：没有执行完整双服务实例的进程强杀、真实网络丢包或提交确认丢失矩阵；相关确定性交错/异常测试不冒充该验收。最后epoch复核后仍有撤销窗口，普通查询不验证任意运维篡改的全库向量；alias无CAS，迟到镜像可能短暂失配，但不决定正式读取。候选/旧版本没有自动GC，实际向量JSON超预算可能在INDEX计费后发现。未推广R2、cap2或扩大TopK，R1/denseTop8/每文档1块/final3维持原配置。

### 下一轮建议：故障演练与候选容量观测

**问题和原因：** 本轮已将读取权威从alias移到MySQL，但真实进程退出、提交确认未知和跨实例迟到alias仍缺完整运行证据；整库复制也会持续积累候选。先补恢复证据和容量观测，比立即删除旧版本或继续增加质量参数更可归因。

**建议流程与模块：** 使用独立随机schema、独立Milvus alias/collection和两套服务构造“准备后退出、提交后确认丢失、旧执行恢复写alias、数据库离线、目标缺失、空白名单”场景。按稳定publicationId查事实，核对head/serving/任务/引用结果及周期对账。增加只读候选清单和总字节/年龄观测，区分当前引用、历史事实引用与未提交候选；首轮只报告，不自动删除。涉及publication Service/worker、Python边界及隔离验收运行器，不扩展模型调用预算。

**选择和边界：** 本轮不自动开始GC，因为“非当前head”不等于无引用，在途读和未知提交仍可能引用旧集合。若后续设计删除策略，需先明确引用保护、隔离期、在途读约束、重入/失败恢复和人工审核；若存在不同删除取舍，另向用户给出具体方案确认。恢复演练不自动重算向量或切旧alias，不用删除故障证据制造成功。

**预定验收：** 所有故障都检查实际文档/索引/激活身份，未知提交不重复付费、不覆盖相同ID事实；停止写入后alias应收敛。容量报告不得输出原文、向量、下载URL或凭据。人工标注校准、多证据cap2与拒答质量评估留作后续独立变量，需有新证据再决定推广。

## 第四轮：故障证据与只读候选容量（2026-11-17，已完成本机验收）

### 问题、选择和实现

上一轮已经上线权威发布，但真实进程退出后的持久状态和迟到alias恢复仍只有部分证据；整库候选积累也缺只读清单。本轮按用户确认设计实施隔离资源演练与手动观测，不引入自动恢复、GC或质量参数推广。仅进程内Mock不能验证进程直接退出；在业务库强杀/删除会影响当前服务，因此使用随机schema/alias和独立执行进程。设计与计划见 `docs/superpowers/specs/2026-11-16-rag-fault-capacity-design.md`、对应plans文件。

Spring新增Bearer内部inventory分页，epoch游标使用既有唯一索引，单页最多100条、额外1条判hasMore，短事务持state锁返回当前模式/epoch/head和不可变事实身份。只投影publicationId/epoch/collectionName/createTime，不传完整receipt或文档名。Python严格核对数字类型、分页排序、唯一身份、游标及跨页控制状态，最多1000条事实；漂移、超限或API故障失败关闭。普通问答不查询此清单。

`knowledge_capacity` CLI显式`--live`才执行只读Spring/Milvus请求，筛选当前alias命名空间和kind/aliasHash，区分CURRENT/HISTORICAL_FACT/NO_OBSERVED_FACT。默认仅看row_count与创建时间；`--measure-json`才有界下载行，复用iterator取消/关闭边界并用剩余JSON预算进一步限制scan。每集合沿原64MiB预算、累计成功测量256MiB；完整批次收到后核对，末批可超过剩余字节，不保证网络流量逐字节上限。超限结果为未知且后续不下载。CLI整体失败只输出固定码，不输出供应商异常、原文、向量、URL或凭据。

### 故障触发、判断和边界

- 子JVM使用真实生产Service/Mapper/事务及随机MySQL schema，在准备完成后提交前，或commit返回后回执前直接`Runtime.halt`退出。父进程独立连接核对fact/head/serving/task，提交前不得伪造成功；提交后确认丢失仍按原ID/原收据返回既有事实，原lease撤销也可确认，冲突收据不得覆盖。该测试不使用完整Spring Boot HTTP服务器、不在事务提交中途强杀、不证明生产执行自动认领或续跑。
- 真实Milvus随机alias测试让独立Python进程迟到写回旧alias；具体新head检索保持原结果，随后按当前head对账恢复镜像。缺失具体目标和空白名单返回无答案/无来源，计数Fake query Embedding和回答模型均0调用。Fake仅替代控制/供应商，不冒充真实业务数据库联通或自然语言质量验收。
- 容量观测读前后复核控制快照和候选名列表。complete仅表示本次观测完成且这些身份稳定；未核验任意候选完成标记、在途读、未知提交或运维绕过后的内容，NO_OBSERVED_FACT不能推导可删除。发现接口先返回集合名列表再筛选，候选1000上限不是服务器全部集合名的流式预算。
- JSON序列化容量与Milvus磁盘/索引/副本容量不同，diskBytes始终null；年龄使用Hybrid Timestamp，非法/未知/未来值为null。观测createTime的数据库字符串不擅自转换时区。

### 验证和运行证据

第一次Python完整回归1634通过、24跳过、2个既有GPU所有权锁测试失败；两项单独重跑通过，检查时无残留Python进程，首次失败尚未稳定复现，不改锁策略掩盖结果。最终完整复验 **1636通过、24跳过、0失败**，compileall/uv lock检查通过；结果及JUnit在 `tmp/round4-python-final.log`、`tmp/round4-python-final.xml`。首次失败日志保留 `tmp/round4-python-full.log`，单项复验为 `tmp/round4-gpu-recheck.log`。期间工作区另有Vue提示任务改动，本轮不归因或提交其代码。

Java目标测试含普通回归、7项ownership隔离MySQL、8项publication隔离MySQL及2项子JVM场景，执行成功且17个随机schema均清理；随后干净编译成功。执行日志 `tmp/round4-java-final.log`、`tmp/round4-java-compile.log`。第一组Python目标101项通过，含4项显式随机Milvus资源测试；后续新增分页/分类测试由完整回归覆盖。资源测试不调用真实回答/Embedding、不碰业务集合。

本机已启动新版Spring并实际运行只读JSON容量观测：epoch仍1，当前publication不变，1个候选、84行（72块+11 manifest+1完成标记），成功序列化字节 **4,601,857**；完整观测、控制及候选列表稳定。与上一轮只统计原83业务行的数字不同；不是容量增长或磁盘占用结论。报告为 `tmp/round4-capacity-live.json`，不提交业务报告/日志/私有数据。本轮不新增schema、不运行INDEX/REBUILD/bootstrap或真实供应商调用。

最后启动Python后复验：首次健康请求早于8000监听建立而连接失败，服务就绪后重新验收成功，未为启动时序修改生产行为。两端健康、11 ACTIVE/1 DISABLED、0未完成任务、原head/epoch/alias和72块/11 manifest原字段及f32向量逐项一致，旧源不变；证据 `tmp/round4-runtime-final.log`、`tmp/b0b-runtime-verification.json`。默认元数据观测另运行成功，不下载向量，报告 `tmp/round4-capacity-metadata.json`；完整JSON测量4,601,857字节的报告不被默认模式的0测量覆盖。当前线上已载入只读inventory接口，Python CLI源码可直接运行，没有新增问答参数或收费调用。

本轮实现/测试/运行交接提交为 `3e2bc53`，设计范围提交 `cf49361`。只提交本轮路径，保留README既有两反引号和用户未跟踪取消计划；另一任务Vue提示提交不属于本轮优化。

### 下一轮说明

优先补完整隔离Spring/Python HTTP协作的故障矩阵：commit已落库但HTTP响应不可达、在途回答期间控制API故障、实例恢复后的稳定ID确认及多实例alias对账。原因是本轮子进程和真实资源分别验证了边界，但尚未将整套HTTP/worker调度串成同一演练。使用随机库/alias、固定合成向量和计数Fake模型，先定义发送前后屏障与可重复响应丢弃；不能通过改ID、切旧alias、重算向量或直接标成功完成恢复。预定逐项核对原事实/head/serving/task/回答来源，明确请求零重试和模型调用数。

自动GC继续等待无引用、未知提交及在途读保护设计；本轮容量报告仅为输入证据，不能直接将历史或无已观察事实集合删除。人工质量校准和cap2/R2推广仍作为独立质量轮次，不以版本测试或容量数字证明答案质量改进。

## 第五轮：跨服务HTTP故障验收（2026-11-17，已完成隔离验收）

### 原问题、方案与实现

第四轮的子JVM和真实Milvus测试分别通过，但真实HTTP客户端、生产Spring控制器及FastAPI app装配尚未串成同一故障验收。本轮采用测试专用Spring Web容器加载生产Controller/Service/Mapper、真实随机MySQL schema，以及独立FastAPI进程连接随机Milvus集合；Java仍独占MySQL，Python只通过HTTP控制面访问事实。回环代理在明确发送点丢弃commit响应，并按模型内测试屏障将snapshot响应改为503，不在正式业务库或网络上注入故障。

固定合成向量和计数Fake模型避免供应商费用、输出不确定及GPU竞争；Disabled checkpoint不代表生产checkpoint恢复验收。测试专用prepare/context/commit/revoke接口只存在test classpath，绑定回环，不添加正式API或生产开关。测试运行入口是 `AI_RAG_HTTP_INTEGRATION=true` 的 `KnowledgePublicationHttpIntegrationTest`，Python运行器为 `ai-service/tests/http_publication_runner.py`。

### 场景、判断和边界

1. Java真实发布事务成功返回后，代理关闭HTTP连接，不发送响应；Python实际httpx报告协议错误，然后用生产Spring客户端按task_1查询持久事实。撤销原lease后显式再次提交同收据仍返回原事实，数据库只存在一条task_1事实，head/serving/task一致。显式第二次请求用于确认，不宣传自动重试或付费重算。
2. 真实FastAPI回答已完成检索及模型调用时，测试模型切换控制代理；模型返回后snapshot实际503。HTTP回答必须无答案、无来源，模型与query Embedding各1次，失败snapshot应仅1次，不自动重试。真实供应商调用为0。
3. 终止该Python实例，恢复控制响应后启动新实例，重新回答并确认来源；另启第二实例分别调用正式alias reconcile，目标均收敛到权威collection。此恢复是运维重启读取，不是任务认领、checkpoint自动续跑或SSE重连。

完整业务Spring profile、生产worker调度/任务接管、Python prepare下载/Embedding、事务中途强杀、随机网络丢包及模型语义质量仍不在本轮。候选完成数据用正式PublicationStore写入并验收，但构建许可为测试进程内许可，不冒充完整Spring prepare许可链路。production源码未改变，本轮是新增测试证据，不能声称降低实际答案错误率或已上线自动恢复。

### 失败及修复记录

首轮测试容器全量自动配置加载Legacy RedisEmbeddingStore，缺业务Redis配置导致启动失败；收窄为Web/JSON自动配置。第二轮合成文档file_type未用内部小写规范，被原严格回读拒绝，修正测试数据。第三轮注入的RAG缺候选存储preparer依赖，对账入口明确拒绝；补齐测试装配而非放宽正式入口。第四轮主流程通过后增加snapshot故障请求计数和双实例对账，最终复验单独记录。失败日志保留 `tmp/round5-http-test.log` 至 `round5-http-test4.log`，不删除失败证据。

最终Java目标 **27通过、0失败/错误/跳过**，含8项隔离publication MySQL、2项子JVM和1项跨服务真实HTTP/Milvus场景，11个随机schema均清理；干净编译通过。日志 `tmp/round5-java-final.log`、`tmp/round5-java-summary.json`、`tmp/round5-java-compile.log`。跨服务报告 `tmp/round5-http-report.json`：commit请求2次（丢响应与显式确认）、故障模型/query Embedding各1次、失败snapshot1次、自动付费重试0、真实供应商调用0，恢复实例确认及双实例mirror收敛成立。随机Milvus集合和alias均由运行器finally精确清理并复核不存在。

Python compileall（含测试运行器）、完整pytest、uv lock检查通过：**1636通过、24跳过、0失败**，日志/JUnit为 `tmp/round5-python-final.log`、`tmp/round5-python-final.xml`。常规Python回归不默认执行跨服务资源测试；上述资源结果来自显式Java入口。测试前停止本轮管理的Python释放GPU锁，验收后离线缓存模式重新启动。生产源码和数据库结构无改动，无需部署测试控制器；本轮只增加可重复验收设施和文档，保留用户README两反引号、取消计划及另一任务阅读验收脚本。

本机服务恢复后只读完整复验通过：两端健康，epoch=1/publication/head/alias不变，11 ACTIVE/1 DISABLED，72块与11 manifest原字段及f32向量逐项一致，旧源保留；日志 `tmp/round5-runtime-final.log`。本轮正式库没有INDEX/REBUILD/bootstrap或模型调用，不把合成HTTP答案当作自然语言质量评估。

第五轮实现、测试、设计及交接提交为 `ea1deb9`；未混入另一任务生成验收脚本或用户既有README修改。

最终已启动新版两端服务，正式只读核对通过：epoch=1、原publication/head/alias不变，11 ACTIVE/1 DISABLED，72块/11 manifest原字段和f32向量逐项一致，旧源未变，两端健康，真实回答/Embedding0；日志tmp/round9-runtime-final.log。本轮已部署仲裁源码和空执行表，不执行正式INDEX/REBUILD/bootstrap，保留用户README两反引号和未跟踪取消计划。

第九轮设计提交6bb6ba2；实现、测试、迁移及运行交接提交e400e8e（feat(rag): 持久仲裁首次准备资格并阻止并发重复计算）。提交检查发现迁移测试文件尾多余空行，随后仅移除尾部空白，不改变已验证行为。

### 下一轮建议

优先补真实worker到prepare-publication的全链路及未知准备响应：Python已经完成候选，但Java未收到收据时，任务保持何种状态、重试是否产生额外候选、当前epoch变化怎样拒绝旧尝试。原因是本轮确认的是数据库commit事实丢响应及读取故障，尚未覆盖付费/候选构建阶段的未知结果。使用隔离任务与可计数合成Embedding，把发送前屏障、完成后断开和claim/lease接管放进同一演练；逐项核对原head、候选数量、事实唯一及调用数。若据此需要新增候选准备幂等/恢复策略，应先给用户说明稳定ID、attempt隔离、费用和存储代价的方案，不直接推广生产行为。自动GC仍需独立无引用保护设计。

## 第六轮：prepare响应未知与真实worker重试（2026-11-17）

### 问题、选择与实现

第五轮验证了commit落库后响应丢失，但没有覆盖Python已完成候选、Java未收到prepare收据的成本窗口。本轮按优先级扩展隔离验收设施，生产策略未改。真实worker的poll负责claim、租约、prepare和commit，测试事务finalizer及生产Controller/Mapper访问随机MySQL schema；独立FastAPI使用正式app factory/ETL/Preparer、真实Spring租约验证及Milvus。合成HTTPS签名地址由本地测试Downloader替代下载，计数Fake Embedding替代供应商。

代理只在prepare返回200、候选已完成后断开首响应。worker按既有瞬态错误处理为PENDING、retryCount=1并撤销租约；测试核对原epoch=1空head未动，再显式推进nextRetryTime并再次poll。第二attempt使用不同operationId、更高fence和不同候选，成功提交task_1唯一事实并推进epoch=2。核对事实完整收据等于第二响应、head指向第二集合、首候选仍存在。初始空候选通过正式PublicationStore建立，但初始化许可为测试替身。

选择先测再设计复用，原因是提交幂等不等于准备幂等，不能仅因publicationId相同跨lease直接发布。新断言明确旧proof仍可验真但current=false；不将首候选完成标记当作当前提交资格。关键文件为HTTP IntegrationTest、Mysql IntegrationTest夹具与http_publication_runner.py；控制入口仅test classpath、回环监听。

### 边界、成本与失败记录

实测两次prepare、两次Fake文档Embedding、零回答模型和零真实供应商调用；集合共三个（初始空集合+两次INDEX）。这证明同任务准备响应未知的重试存在重复计算和候选积累路径，不能据Fake调用次数推算真实费用或延迟。首尝试失败后旧lease已撤销，原head保留，最终数据库只发布第二收据。本轮未直接提交旧收据测试其拒绝，也未覆盖claim接管、并发epoch变更、prepare中途退出、定时调度和真正供应商。不能宣称自动任务恢复、准备缓存或GC已实现。

首次执行在prepare前被INVALID_REQUEST拒绝：测试OSS替身使用HTTP地址，正式schema只接受HTTPS；修正合成地址后通过，没有放宽生产校验。失败日志为tmp/round6-prepare-test.log，修正复验为tmp/round6-prepare-test2.log。资源测试使用随机命名空间并finally清理、复核集合不存在；不删除正式历史候选。

### 验证与交付状态

最终Java目标80项通过、0失败/错误/跳过，包含2项HTTP、8项隔离MySQL及2项子JVM场景，12个随机schema清理；日志tmp/round6-java-combined.log和round6-java-summary.json。首次组合命令误用资源开关导致MySQL组跳过，随后以AI_KNOWLEDGE_PUBLICATION_MYSQL_INTEGRATION=true明确补跑并最终合并复验，不将跳过写成通过。干净编译成功，日志tmp/round6-java-compile.log。Python compileall（含运行器）、uv lock检查及完整pytest通过，1636 passed/24 skipped/0 failed，日志与JUnit为tmp/round6-python-final.log/xml。报告tmp/round6-prepare-report.json逐项记录首响应丢弃、两次Embedding、三个候选、旧lease撤销、attempt隔离和第二收据发布。

停止本轮管理的Python释放GPU锁后运行完整回归，完成后重新启动。正式知识库只读完整核对通过：两端健康、epoch=1、原publication/head/alias不变，11 ACTIVE/1 DISABLED、72块/11 manifest原字段和f32向量逐项一致、旧源未变；日志tmp/round6-runtime-final.log。真实回答/Embedding调用均0。本轮无生产源码或数据库结构变动，无需部署测试接口；README新增验收说明，保留用户既有两反引号修改及未跟踪取消计划。

第六轮测试、方案和交接提交为 `c14d8ad`（test(rag): 验证准备响应丢失后的真实任务重试与重复成本）。

### 下一轮方案与优先级

P1建议实现“可核验准备结果恢复”：Spring在发送前持久化attempt身份，Python提供受认证、有界的准备结果查询；响应未知先按原attempt查询完成证据。重试取得新租约后，重新核对baseEpoch、源collection、文档内容/索引/激活身份、Embedding模型/维度及完整候选快照。满足条件时将已验收向量复制到新attempt私有候选并产生新收据，最终仍走原事务提交。旧候选和旧收据不直接获得新lease权限。目标减少重复Embedding，仍承担整库复制/回读与状态维护成本，需设计未完成/部分写入/查询故障的显式分支。

另一方案继续每次新attempt重新构建，流程和隔离较简单，但保留重复计算/存储成本；只在同一有效lease重传缓存收据的方案实施较小，无法覆盖当前worker finally撤lease后的下一次执行。推荐可核验恢复，因为它直接对应本轮实际故障窗口；存在不同生产代价，实施前按AGENTS.md待用户确认。

预定验收：首次完成丢响应后仅一次Embedding，新attempt仍拥有独立收据和唯一事实；epoch漂移、文档撤销/修改、模型变化、旧lease失效、结果缺失/未完成/损坏、重复请求及进程重启均不得绕过现有授权。查询失败不自动证明需要重算，不得通过换账本绕过调用预算。先定义允许重算或停止的规则再实现。P2自动GC需独立引用与隔离期设计，NO_OBSERVED_FACT不能作为删除授权；P3再用人工标注质量集评估cap2/R2，当前R1/denseTop8/cap1/final3保持。

## 第七轮：可核验INDEX准备结果恢复（2026-11-17）

### 原问题、方案与实现

第六轮实测prepare候选完成后响应丢失，worker重试两次Embedding并生成两份INDEX候选；用户明确确认可核验恢复方案。本轮实现首次身份台账、完成证据读取及新lease向量复用。Spring发送前在epoch→task→lease锁序下登记首次context/operationId/fence到customer_service_knowledge_prepare_anchor，同任务不可覆盖；不保存proof、签名URL或向量，Python不连接MySQL。登记确认未知不得发送HTTP或写成执行失败。

Python prepare可选prepareAnchor，INDEX首次候选名由规范context/operationId/fence域分隔hash确定，定位单个旧候选，不扫描全库。重试先验证新lease和HTTP权威上下文，再读取旧完成标记、完整manifest/块/f32向量/预算，核对旧attempt和全部文档身份。重新下载、hash校验、按当前配置解析目标原文；逐文本取原向量，再逐字段核对块/定位/规范行和整库hash。验收一致才复制为新attempt候选，产生当前operationId/fence收据，提交继续原MySQL事务仲裁。准备后HTTP再次核对context；最后仍有并发撤销窗口，由commit裁决。当前attempt已完成的重传完整验收后返回原收据。

选择此方案可跨Python重启复用，并保留新租约隔离；只缓存同lease收据不能覆盖worker finally撤销后的下一执行。代价是新台账、原文重下载/解析、完整回读和整库复制；两次INDEX仍有两份候选。没有降低真实计费单价、保证供应商exactly-once或减少存储集合数量。旧客户端无anchor继续旧流程，DELETE/REBUILD不引入恢复缓存。

### 边界与故障处理

- 缺失、半写、损坏、模型/维度变化、context或文档身份漂移明确失败关闭，不自动切回真实Embedding。首次完成前退出可能已经付费而无证据，需新INDEX任务重算，不能认定免费失败。
- anchor只定位旧证据，不能续旧lease或直接激活旧候选；新lease依旧必须当前。当前候选已存在但未完成不得覆盖；同名并发创建可能拒绝，不新增跨实例锁或任意重试。anchor首记录受Spring短事务保护。
- epoch漂移登记拒绝，旧记录保留；数据库确认未知保留未知而不是执行失败。恢复错误沿既有有上限重试，重复恢复不调用Embedding；未新增生产任务自动接管或恢复调度。
- 恢复不提供独立裸收据查询API：正式prepare入口在当前授权下定位并验收旧候选，然后返回新收据，避免未经当前lease的复用路径。没有GC，历史/失败候选与anchor继续保留。

### 验证、迁移和交付状态

Python新增9个边界测试，目标组45项通过；完整pytest **1645通过、24跳过、0失败**，compileall/uv lock通过，日志tmp/round7-python-target.log、round7-python-final.log/xml。覆盖同attempt重传、新Preparer对象恢复、旧结果缺失/半写/向量损坏/模型变化/定位变化及context形状拒绝。未将Fake或固定原文作为语义质量验收。

Java最终目标81项通过，追加登记确认未知的worker测试复验37项通过，合计82项当前目标通过、0失败/错误/跳过；资源组含2项HTTP、9项隔离MySQL及2项子JVM，13个随机schema清理。日志tmp/round7-java-final.log、round7-java-worker-final.log及summary.json；干净编译成功，tmp/round7-java-compile.log。首次资源组另成功运行，tmp/round7-java-resources.log。

HTTP场景首次prepare200后丢响应、原head保留、旧lease撤销，终止并重启Python，再由真实worker重试。旧anchor不变、新operationId/fence/集合独立、唯一事实为第二收据。两次prepare合计Fake Embedding **1次**（前轮2次），重启后0次、回答/真实供应商0次；候选仍3个（初始空+两次INDEX）。报告tmp/round7-prepare-report.json，前轮报告保留。没有强杀Spring claim接管、随机丢包、真实供应商或并发创建矩阵验收。

正式环境检查0未完成任务后停止Spring；维护入口备份8张知识库表为私有JSON（含DDL/字段顺序/NULL/全部值）并保存SHA256，再只新增空anchor表，逐表核对已有结构和数据未变。记录tmp/round7-migration.log，备份tmp/knowledge-prepare-backup-94ffef9a-8e22-4d81-9d13-ddd47ed4ef0d。备份不是自动恢复工具，恢复需单独核查，不提交原文/凭据。新库init_database.sql同步，已有库执行alter_customer_service_knowledge_prepare_anchor.sql；publication模式缺表启动拒绝，旧模式不开恢复。

最终已启动新版Spring/Python，本机只读完整核对成功：epoch=1、原head/publication/alias不变，11 ACTIVE/1 DISABLED，72块/11 manifest所有原字段与f32向量逐项一致，旧源未变，两端健康，真实模型/Embedding调用0。日志tmp/round7-runtime-final.log。本轮已部署恢复源码及空anchor表，没有执行正式INDEX/REBUILD/bootstrap。保留用户README两反引号及未跟踪取消计划，不混入提交。

第七轮设计提交bd12ba1；实现、测试、迁移及运行交接提交eb8386a（feat(rag): 恢复已验收准备结果并避免重试重复向量计算）。

### 下一轮优先项、原因和验收

P1补恢复并发/进程退出边界：首候选半写退出、同attempt并发、epoch改变与恢复写入交错、Spring登记确认未知后重启、替代claim拒绝旧执行。原因是本轮已证明完整候选可跨Python重启恢复，但半写和跨实例竞态主要是离线边界。使用随机schema/alias、发送/完成屏障和计数替身，验收绝不覆盖旧候选、不错误激活、不额外Embedding。首轮只补证据，不自动增加续跑或重算策略。

P2设计人工可审核GC：本轮减少了Embedding但集合仍积累。先确定事实引用、在途读、未知prepare/commit及anchor引用的保护，再决定隔离期和删除重入流程；NO_OBSERVED_FACT不等于无引用。P3人工标注拒答/多证据质量评估及cap2/R2单变量对照；R1/denseTop8/cap1/final3仍保持。生产策略存在多个不同成本选择时再具体说明并确认，本轮已批准恢复方案无需重复授权。

## 第八轮：恢复并发、半写退出与旧执行拒绝（2026-11-17）

### 原问题、选择与实现

第七轮证明完整候选可跨Python重启恢复，但半写进程退出和首次并发仍缺证据。本轮按用户授权优先补隔离验收，未修改生产策略。测试流程分别验证Milvus持久候选、Spring持久anchor和业务发布仲裁，不将这些独立测试称为完整HTTP并发矩阵。计划为docs/superpowers/plans/2026-11-17-rag-recovery-fault-matrix.md。

Python用两个独立Preparer/Store与同一有效测试许可，Embedding入口屏障让两个首次请求均在集合创建前到达。第二请求等第一完成后继续写，确定性名称拒绝覆盖；完整重传返回第一收据且不新增调用。用非串行许可替代Fake默认scope锁，是为了暴露HTTP租约适配器只验权、不串行化同lease请求的真实边界；许可仍是测试替身，不验证Spring跨实例授权。

真实随机Milvus子进程使用正式Preparer/PublicationStore，在首批块insert正常结束后os._exit(79)，跳过finally和完成标记。父进程独立读取持久集合，确认有块而无完成证据；以新attempt恢复必须KNOWLEDGE_PUBLICATION_INCOMPLETE，计数Embedding0，不覆盖旧行、不创建新attempt集合。父fixture登记确定性目标，清理前复核随机aliasHash/kind，再删除并检查不存在。使用合成文档和向量，不调用供应商。

Java新增子JVManchor登记后Runtime.halt(77)：父连接再次取得同anchor，新lease仍返回首次身份，无task_1发布事实且head epoch1。MySQL替代claim测试显式更新隔离owner、撤旧lease并取得替代lease，旧登记/提交必须CLAIM_LOST，替代owner可以读取原anchor且自身PROCESSING状态不被旧执行改写。epoch变更后旧收据commit必须EPOCH_CONFLICT，不写事实/serving，anchor保留。此为明确构造已接管状态，不实现自动认领。

### 发现、成本与不适用边界

实测首次并发可以发生两次Fake Embedding，而后仅一份完成候选；再次重传调用数仍2。这证明确定性集合名只防覆盖，无法拦截创建前重复付费。第七轮普通顺序重试1次Embedding的成绩仍有效，两种触发不同，不能合并成计费exactly-once。名称冲突会失败，当前没有跨实例prepare执行锁。半写退出的父恢复Embedding0只表示恢复没再调用，不说明子进程退出前免费。

本轮没有发现发布覆盖或旧claim越权缺陷，生产代码无修改、无新增schema或正式INDEX/REBUILD。没有在MySQL事务中途强杀，没有真实HTTP同lease并发、网络分区、进程长期暂停或claim自动接管；epoch用隔离数据库顺序交错，不宣称覆盖任意原子性。真实Milvus半写测试替代租约、不访问MySQL，与Java持久测试分开报告。

### 验证与失败记录

Java资源组14项通过（11项隔离MySQL、3项子JVM），0失败/错误/跳过，14个随机schema清理；父进程补强先直接读取anchor存在证据后再register，子JVM三项重新通过。日志tmp/round8-java-resources.log、round8-java-summary.json、round8-java-process-final.log，最终干净编译tmp/round8-java-compile-final.log成功。Python并发目标1项通过，真实Milvus子进程1项通过，元数据确认后精确清理；日志tmp/round8-python-target.log、round8-milvus-process.log。

首次Python完整回归1645通过、25跳过、1失败：既有test_real_rag_diagnostic_preserves_timeout_and_degradation_after_drain的disconnect分支同时设置30ms timeout，返回CUSTOMER_SERVICE_TIMEOUT而不是预期取消。源码确认断连需等待模型entered，超时可在调度前先发生；该组单独3项复验通过，尚未稳定复现，不修改生产超时策略或删除失败证据。日志tmp/round8-python-final.log/xml、round8-timeout-recheck.log；完整复验另记，不用后续成功覆盖首次结果。

最终完整Python复验1646通过、25跳过、0失败，日志tmp/round8-python-recheck.log/xml；compileall与uv lock检查通过。25跳过包括新增显式Milvus进程场景，实际资源结果由单独opt-in运行提供。首次失败及单项复验保留，不宣称竞争已修复。测试完成后恢复本轮管理的Python，Spring始终保持运行；生产源码和schema未变，不部署测试入口。

正式库最终只读核对通过：两端健康，epoch=1、原publication/head/alias不变，11 ACTIVE/1 DISABLED，72块/11 manifest原字段及f32向量一致，旧源未变，模型/Embedding调用0；日志tmp/round8-runtime-final.log。保留用户README两反引号和未跟踪取消计划。本轮增加验收设施，不声称答案质量提升或自动恢复上线。

第八轮验收设施、计划、运行证据及交接提交82ff0f6（test(rag): 补恢复并发半写退出及旧执行拒绝验收）。

### 下一轮建议、原因与验收

P1针对首次并发重复付费设计跨实例prepare执行仲裁。推荐先做具体方案：独立持久attempt状态，只有执行所有者可以开始Embedding；同attempt其他请求读取COMPLETE或明确BUSY/UNKNOWN，不能超时后自动重算。执行超时和进程退出可能保留UNKNOWN，需要新INDEX或经审查的恢复；不得把锁超时等同于未调用供应商。代价是台账/所有者/状态维护与故障运维，收益是收窄本轮已证实的创建前重复调用窗口。

另一方案限制为Spring同进程请求串行或保留当前冲突拒绝，改动较小，但无法涵盖多实例/重启重复请求。生产仲裁取舍需具体设计并按AGENTS.md确认后实施，不能直接套Redis TTL锁宣称exactly-once。预定验收同lease并发、跨实例、所有者退出/暂停、完成回执丢失、DB离线和epoch撤销，每次记录发送前持久事实和Fake调用数；不提高真实供应商预算。P2GC仍需引用保护，P3质量标注和cap2/R2继续独立验证。

## 第九轮：持久首次准备执行仲裁（2026-11-17）

### 原问题、选择和实现

用户选择方案5。第八轮证实两个首次prepare可在集合创建前各自Embedding，确定性名称只防覆盖。本轮Spring新增customer_service_knowledge_prepare_execution，以taskId唯一记录首次operationId/fence、随机executorId、STARTED/UNKNOWN/COMPLETE、观察期限和可选收据，不存proof/URL/正文/向量。BEGIN在epoch→task→lease短事务核对业务context、首次anchor、有效claim/lease后只首次返回granted=true；所有重复请求包括同executor均不重授，STARTED期限内BUSY、固定300秒后观察UNKNOWN。Python不连接MySQL。

Python正式app装配Spring仲裁客户端，anchor INDEX缺装配明确失败。首次候选不存在且非恢复时申请一次资格；响应异常不重发、不Embedding。进入付费前再次复核lease/epoch，下载解析期间撤销也能阻止后续调用。候选完整验收后原executor登记COMPLETE；同完整收据幂等，不同executor拒绝。实际异常/cancel不额外发送收尾RPC，保留STARTED直至观察未知，接口允许原executor显式报UNKNOWN。资格不因期限、断连或状态未知再次授予。

完整Milvus候选仍优先经过原回读验收并恢复，新lease复制向量和原提交仲裁保持；执行台账不是发布授权。COMPLETE只是可信Python登记的结果，不能代替Milvus完整验收。收据保存前检查64KiB；控制HTTP有界读取、严格schema，不随故障重发BEGIN。主要模块为KnowledgePublicationService/Mapper/Controller、SpringKnowledgePublicationClient、KnowledgePublicationPreparer与app装配；设计6bb6ba2。

### 取舍、边界与限制

收益是同一准备身份首次多实例争用只授予一执行者；代价是授权后崩溃且无完整候选时可能停住，即使尚未付费也不自动重算。UNKNOWN不等于失败或可接管，不新增执行恢复调度。记录不是供应商计费exactly-once：无anchor旧请求、DELETE/REBUILD、直接未装配离线Preparer、供应商内部重试及人为新INDEX任务都不在保证内。候选恢复仍会复制新集合，没有GC。

300秒只是固定观察期限，未新增心跳；期限内原executor完成可以登记，期限后也不自动剥夺其已授资格，当前lease/epoch依然决定可否登记或发布。已授资格到实际外部发送仍有撤销窗口，再复核不构成跨资源原子事务。资格请求确认丢失不通过重新BEGIN取granted；无完整候选时旧执行保留未知。长期暂停/网络分区/事务中途退出等完整进程矩阵尚未执行。

### 验证、失败记录与部署

Python新增10项仲裁及客户端测试；最终完整1656通过、25跳过、0失败，compileall/uv lock通过，日志tmp/round9-python-final.log/xml。覆盖BEGIN响应断连/503/超限/错误grant/status只请求一次，未授予零Embedding；独立Preparer共享资格只一次Embedding、完整重传复用。付费前权威复核的离线并发夹具使用AsyncMock，实际HTTP资源单独覆盖。最初客户端命令误在根目录执行无pytest环境，随后在ai-service正确运行通过，未因环境问题改代码。

Java最终目标88项通过、0失败/错误/跳过，含14项隔离MySQL、3项子JVM、2项HTTP；19个随机schema清理，日志tmp/round9-java-final.log、summary.json。覆盖MySQL双线程BEGIN仅一个grant、同executor重传不重授、观察过期UNKNOWN、完成重复幂等、其他executor拒绝、epoch/撤lease拒绝。尚无真实完成登记响应丢失或授权事务中途强杀；客户端断连测试不冒充该矩阵。干净编译日志tmp/round9-java-compile.log。

隔离HTTP runner启动两个独立Python app进程，代理同时向二者发送同一当前lease/anchor请求；计数合计Embedding1次，一个200另一个503/KNOWLEDGE_PREPARE_BUSY。丢弃成功prepare响应，worker保持PENDING/原head；重启主实例、新attempt恢复后0新增Embedding，最终唯一第二收据事实。报告tmp/round9-prepare-report.json，候选仍3个，真实供应商/回答0。原commit丢响应和读取故障HTTP场景亦通过。新增付费前复核后重新执行最终HTTP而非沿用首轮证据。

正式库预检0未完成任务后停止Spring、备份九张知识表（包括anchor）为私有JSON并保存hash，只新增空执行台账，迁移后逐表结构与数据一致。迁移记录tmp/round9-migration.log，备份tmp/knowledge-execution-backup-04db2b22-79db-4da0-a933-cd97792ef22e。新表SQL及init同步，publication模式缺表启动拒绝。备份不提交，不是自动恢复工具。Python完整回归前停止管理实例释放GPU锁，完成已重启；Spring待编译完成后恢复。

### 下一轮建议

优先补持久仲裁故障矩阵与只读状态观测：BEGIN已提交但响应丢失、执行者在授权后/Embedding后退出、COMPLETE登记响应丢失、长暂停后恢复、epoch撤销和控制面不可用。原因是本轮已有并发成功与客户端异常证据，尚缺这些发送点的完整跨服务退出矩阵；UNKNOWN需可解释观测，不能人工盲目新建任务。建议随机schema/alias屏障及计数替身，新增管理员只读执行状态/固定原因/时间投影，不输出原文/proof/URL；先只观察，不自动接管/重算。涉及生产观测新接口可在明确设计后实施，恢复选择不同需另确认。

GC继续等待事实/anchor/执行台账与在途读引用保护；质量cap2/R2和人工标注仍独立轮次。既有断连测试30ms竞争未改，本轮完整回归通过不能证明竞争已消失。

## 第十轮：准备仲裁退出与确认丢失验收（2026-11-17，观测接口待确认）

### 问题、原因与本轮范围

第九轮只授一次资格，但仍缺授权/完成提交后进程直接退出的持久事实证据。用户授权继续下一轮，本轮先补交接中已提出的故障验收；新增管理员只读接口有单任务查询、全库分页、仅人工查询三种成本选择，已提交具体设计并通过异步问题等待确认，当前没有修改生产接口。设计与计划为docs/superpowers/specs/2026-11-17-rag-prepare-observation-design.md和plans/2026-11-17-rag-prepare-observation.md。

### 怎么验证与边界处理

子JVM复用真实事务，在BEGIN或COMPLETE成功返回后、外部确认前halt(77)。父独立连接先直接核对持久行，再请求：BEGIN退出保留STARTED，重传BUSY；人为推进观察期限得到UNKNOWN，持久行仍STARTED；有效原lease下原executor仍可完成并幂等。COMPLETE退出后父先核对原收据，再幂等完成与BEGIN不重授。两者都没有发布事实，epoch保留1。新增MySQL测试先授资格，再撤epoch或lease，COMPLETE拒绝且STARTED保留、无发布。

Python新增7项离线场景：授权替身先保存再丢确认；下载/Embedding有界Event屏障下取消；Embedding返回后候选构建异常；完成替身先存收据再丢确认；付费前epoch失效；下载期间撤lease。失联不发额外UNKNOWN。未完成候选同attempt不重算，新attempt缺原结果失败；完整候选在新对象/新attempt恢复零新增调用。计数仅Fake，Java子JVM与Python离线各自报告，不冒称完整HTTP网络分区、真实Python硬退出或供应商计费exactly-once。长期暂停只模拟观察到期后的原执行，不是实际暂停进程；控制面异常仍由离线客户端原测试覆盖。

### 本轮验证与交付状态

Python目标17通过；包含准备、恢复、故障和候选六文件的相关回归63通过、0失败/错误/跳过，日志tmp/round10-python-target.log/xml与round10-python-final.log/xml；新增文件compileall及uv lock通过。Java最终MySQL16、子JVM5共21通过、0失败/错误/跳过，21随机schema清理，tmp/round10-java-resources-final.log。首次Java命令的wrapper调用未找到，改用本机mvn执行成功，无生产修复。未重新运行完整Python基线或HTTP矩阵；没有用第九轮1656成绩作本轮结果。

正式只读完整核对成功，tmp/round10-runtime-final.log：两端健康，epoch1、原publication/head/alias不变，11 ACTIVE/1 DISABLED，72块/11manifest全部原字段和f32一致，旧源未变，模型/Embedding调用0。本轮无schema迁移、INDEX/REBUILD/GC或服务部署，测试入口仅测试classpath。保留用户README两反引号和取消计划。必要差异检查后提交本轮验收、设计、计划与交接；提交哈希在本轮最终回复提供。

### 待确认与下一步

推荐管理员按文档/任务联合归属查询的单任务只读接口，查询量固定，只投影固定状态/原因与UTC观察时间/期限，不读receiptJson或凭据、不加FOR UPDATE、不写超时、不触发恢复。全库分页利于巡检但增加扫描/索引/一致性设计；仅人工SQL改动最少但容易误读UNKNOWN。AGENTS交付前检查要求多个生产选择先确认，因此接口尚未实现，收到选择后继续本轮实现及权限/边界验收。COMPLETE不代表已发布；无记录不证明未付费，UNKNOWN不授权接管。后续真实双服务丢确认/退出矩阵仍有缺口，GC须先证明引用保护；质量cap2/R2与人工标注继续独立轮次。

### 第十轮续：单任务只读观测完成

用户明确确认推荐单任务接口后实施。新增管理员GET `/api/admin/customer-service/knowledge/{id}/tasks/{taskId}/prepare-execution`，由独立KnowledgePrepareObservationService调用Mapper一次LEFT JOIN，以task主键及documentId核对归属。只查询taskId/status/observationDeadline，不获取receiptJson/executorId/operationId，无FOR UPDATE、无写入或模型调用，无新增schema。返回recordedState、observationState、固定reasonCode和UTC字符串observedAt/observationDeadline；无记录NOT_REGISTERED不证明未付费，完整登记COMPLETE不代表发布。未知状态归一INVALID/UNKNOWN，STARTED缺期限也明确无效。归属不符NOT_FOUND，非法ID参数错误，数据库故障传播而非伪装未登记。

新增真实权限切面测试拒绝未登录/普通用户、允许管理员，严格六字段及UTC格式；状态期限等号边界、无写入、坏状态、无记录与数据库不可用覆盖。真实隔离MySQL连接锁住执行行，另一线程只读查询5秒内完成，证明没有行锁等待。首轮权限夹具重新when覆盖旧thenThrow触发未登录异常，改用doReturn后复验通过，未修改生产权限；本机脚本首次将既有字符串文档ID加1报类型错误，转换int后通过，无生产改动。日志保留tmp/round10-observation-tests.log及runtime.log。

最终Java32项通过、0失败/错误/跳过（单元权限/观测4、MySQL17、子JVM5、发布边界6），22随机schema清理，tmp/round10-observation-final.log；Python运行行为未改，沿用本轮此前相关63项验收，不冒充完整全库回归。预检0未完成任务后重启Spring，新版已启用，Python持续健康。本机管理员观测、匿名拒绝、错归属与非法参数验收通过，tmp/round10-observation-runtime-final.log；历史任务均无新执行记录，生产BUSY/UNKNOWN/COMPLETE由隔离测试证明，不手工篡改正式库。完整原文/f32/活动身份核对通过，tmp/round10-observation-integrity.log，真实调用0。README/AGENTS同步，本轮新接口在Spring端，不改Python配置或运行说明，保留用户README既有格式变更。

下一轮优先剩余跨服务故障矩阵：真实HTTP BEGIN已提交响应丢失、Python授权后/Embedding后硬退出、COMPLETE确认丢失、长期暂停及控制面失联。原因是当前子JVM持久事实、Python协作取消及客户端丢确认分别成立，仍缺这些组合端到端证明。采用随机schema/alias、固定发送屏障与持久计数，不增加自动接管、重算或真实模型预算；GC引用保护及答案质量独立推进。第十轮此前提交431fa7c，本次接口提交哈希见最终回复。

## 第十一轮：跨服务准备丢确认与Python硬退出（2026-11-17）

### 原问题、选择与实现

用户授权继续第十轮建议。此前MySQL子JVM持久事实、Python协作取消与客户端丢确认分开验证，缺少组合端到端证据。本轮新增prepare_http_fault_runner.py和Java四项显式HTTP场景；不修改生产计算/恢复策略或schema。正式Spring Controller/Service/Mapper连接随机schema，独立Python app使用正式装配及租约客户端、真实Milvus随机alias，下载器返回合成原文，Embedding使用三维计数替身。相较继续离线测试，成本为跨进程启动与真实资源回读，收益是核对HTTP确认与持久事实的组合。

BEGIN/COMPLETE代理在正式事务返回成功后丢弃socket响应；BEGIN只发一次，重启后的另一请求可以查询但不得获准，累计BEGIN2且调用0。COMPLETE首次登记成功但Python收到异常，完整候选已存在，重启请求回读返回原收据，BEGIN1/COMPLETE1、总调用1且无额外登记。下载入口位于授资格后，os._exit(79)模拟授权后硬退出；PublicationStore.write入口位于Embedding返回后，测试进程替换该入口直接退出。退出码79必须明确核对；发送前计数文件flush/fsync，退出后父读取，授权退出0、Embedding后退出1。两者重启同attempt返回BUSY，没有候选、没有新调用。

### 边界与验证

四场景原head/epoch1保留，未写task_1发布事实。STARTED不因退出变为可接管；COMPLETE只登记收据不激活。每例随机schema/alias精确清理，测试路由只存在测试classpath/回环容器。此为真实HTTP/MySQL/Milvus加Fake供应商，不代表真实计费exactly-once或答案质量；计数是测试预占，不是供应商账单。不包括Spring事务中途退出、长暂停、网络分区、控制面长期离线、自动任务认领、供应商内部重试、新attempt人工重算。

首场景URL错用斜杠返回404未进入注入点，修正为正式knowledge:prepare-publication后复验；首矩阵2通过2失败，硬退出在Windows表现httpx.ReadError，原夹具只接受RemoteProtocolError，补接受读取断连仍严格核对退出码与持久事实，非生产改动。失败日志tmp/round11-begin-test.log与round11-http-tests.log及随机runner.log保留。最终四项HTTP全通过、0失败/错误/跳过，四随机schema及所有本例候选清理；日志tmp/round11-http-final.log，报告tmp/round11-begin-loss.json、exit-grant.json、exit-embedding.json、complete-loss.json。Python相关四文件26通过0失败/错误/跳过，tmp/round11-python.log/xml；新增脚本compileall/uv lock通过，Java干净编译另记录tmp/round11-compile.log。未重跑完整Python或原两项HTTP，不把旧成绩写成本轮。

正式只读完整核对通过，tmp/round11-runtime.log：两端健康、epoch1、原publication/head/alias及11ACTIVE/1DISABLED一致，72块/11manifest全部原字段/f32一致，旧源未变，真实聊天/Embedding0。生产源码和schema不改，无部署/INDEX/REBUILD/GC。保留用户README格式及取消计划，中文提交哈希见最终交付回复。

### 下一轮建议

优先补有界长期暂停/控制面失联矩阵：真实执行在授资格后暂停，观测过期不重新授权，恢复后当前lease/epoch有效才可继续；期限后撤销应拒绝完成/发布。控制面离线分别注入付费前和付费后，记录已发送调用与候选完整性，不能把失败等同未付费。这些边界比自动接管更优先，因为本轮只证明退出/重启，没有证明活执行者暂停时的交错。可沿用测试设施，先补证据不自动重算；人工恢复策略与GC仍需引用保护和具体方案确认，质量标注/cap2/R2独立推进。

## 第十二轮：发布补验与单变量重排对照（2026-11-18，持续更新）

### 原问题、方案与已完成范围

用户已确认先补同文档晚提交与 worker 提交确认测试，再做同条件重排开关对照，根据失败原因选择后续优化。本轮按此顺序执行；11 月 16–17 日质量与资源结果仅为历史依据，不记成本轮重新验证。

工作包 A 已提交 `e8051cdbb739588e789b06ede9dddb55b26d31e9`。没有修改生产发布协议。复用随机 MySQL schema、真实事务代理与 Mapper，管理替换使用真实 upload，候选默认计数替身。A 在事务外双 latch 暂停，签发方与 worker 共享可控时钟，让旧 lease 自然过期而 claim 仍当前；B 正常认领并发布 V2 后，通过独立连接保存完整权威状态。恢复 A 并走完真实异常和 finally 后逐列比较，精确断言 LEASE_INVALID、旧任务零发布/镜像事实；独立用例保留有效替代 lease，验证迟到撤销不影响替代执行。另保留 claim/lease/文档身份当前的独立 EPOCH_CONFLICT 用例。

A2 在真实 commit 取得权威行锁后暂停，通过数据库锁等待视图核对管理连接、阻塞连接及本例 state 表，先放行发布再等待管理更新；最终状态符合实际提交顺序。没有以 sleep 或 Future 尚未完成代替锁证据。A3 在真实事务返回并提交后才抛确认异常：匹配事实确认成功、不重复候选或推进 epoch；事实不可查询时分别验证已提交保持 SUCCEEDED、未提交保持 PROCESSING，不擅自失败或恢复。

真实 Milvus 沿同一 A1 时序，使用合成向量准备 bootstrap/A/B 三个私有集合，B 发布后及 A 收尾后按同一 B 快照各读取一次，均固定 V2。集合和 alias 使用随机命名空间，创建 RPC 前登记精确归属，退出时核验元数据后清理。Windows 虚拟环境解释器仍有 launcher 后代，新增只针对本例进程树的超时清理与实际派生子进程探针；未确认全部退出时禁止与写入者并发清理集合。

### A 的本轮验证、失败记录与边界

最终新夹具资源及进程探针共 **9 通过、0 失败/错误/跳过**；随后探针缺少 .venv 时明确跳过的入口调整再验 1/1。相关 Java 命令共 116 项，109 通过、7 跳过（6 项既有 HTTP 集成未开启及该命令未启用的 Milvus 专项，专项另在最终矩阵实跑）。`mvn clean -DskipTests compile` 通过。Python 全套 1663 通过、25 跳过、2 条依赖弃用警告，compileall 和 uv lock --check 通过；这是 B 新工具实现前的本轮结果，后续 Python 改动需重新验证。

原始命令、汇总、XML 和失败日志保存在忽略目录 `tmp/rag-work-package-a-20261118/`。首次设施失败包括 Flex 数据源/全局配置与注解雪花 ID 注册、反射泛型重载、JavaTime 证据序列化和 Windows 输出编码；均修正后复验，不将这些失败当作生产缺陷。全部本例随机 Milvus 资源清理记录为 true，最终 8 个随机 schema 清理。规格与独立质量审查通过；质量审查保留轻微限制：公共 latch 为 45 秒，慢于该预算的 Milvus 操作可能造成设施假失败，不能据这类超时认定发布协议失效。

本包没有真实供应商、Embedding 或 OSS 下载；真实 Milvus 候选使用合成向量。它不证明完整双服务 HTTP 故障矩阵、自动恢复、计费 exactly-once 或全部并发时序。

### B 的前置审计与当前状态

已重新校验固定 90 题与 11 篇公共正文；81 可答/9 拒答及历史问题保持不变。代理前置语义审计 draft-1 整理出 164 条必要事实（137 条公开事实、27 条拒答协议要求）、22 条可选补充、159 条逐项精确引文。独立审阅后在答案生成前，将自动路由机制、看板示例的空状态/数据、后续预览排查、规划日期提醒、确认发送、访问权限和切换筛选后查看列表等七项补充移为可选。新的版本化 draft-2 保留旧 factId/新 factId、原因及历史标注，最终为 **157 条必要事实（130 条公开事实、27 条协议）、29 条可选、159 条精确引文**；另修正三处完整等价表达、部署后图片不显示的场景和下载无需先部署的源码补核说明。原 tmp draft-1 不覆盖；全部仍为代理审阅、待人工确认，不是人工 gold。

Docker/Redis 已运行，Spring 由本轮按用户授权启动，并关闭客服后台 worker 供只读盘点。当前权威 snapshot 为 epoch 1、11 文档，collection 为 `customer_service_knowledge_pub_a5a7821339274e29a82f241ffaaad8e5`。随后零付费预检经 Spring snapshot、PublicationStore.completed/validate_publication 及本地重新解析分块，确认完整内容与向量收据、11 文档/72 块映射一致，前后 snapshot 相同。记录为 `tmp/rag-verification-20261118/preflight.json`，rubric 指纹 `79d2cf63949c8de69877f9c49d4b66c1f834f5ee9b64ce9fcb48d70e1aa54363`，规范行指纹 `644a3b6af98719d3e069465df5f33717e12ebe26d09dab8549d911119c85350a`；正式 runner 仍需重新冻结本次运行。初次预检定位到新 runner 的 HttpUrl 未转字符串导致客户端构造失败，未发供应商请求；修正由 B 包补回归。Python API 未启动，独立 runner 自持 BGE，避免 GPU 双 owner。

B 工具提交为 `f812751ae0b487de2bb269597747c58d867a2430`：同次 Milvus Top8，rerank_off 保持向量顺序、rerank_on 使用 BGE 顺序，其余正式过滤、每文档一块/最多三篇、R1 提示和预算一致。核心授权限定 180 次回答，查询 Embedding 90 输入/3 请求单列；不启用额外付费裁判、稳定性轮次或生产参数优化。所有启动前审查、离线测试与配置预检均无真实聊天或 Embedding 调用，且固定授权锚点不存在。正式批次结果另列，不能把工具、标注或计划写成质量提升。

B 新工具后的全套 Python 首次和第二次均在既有 `test_timeout_returns_promptly_but_holds_permit_until_gpu_work_finishes` 的恢复调用触发 30ms 超时，原 `pytest.log/xml` 与 `pytest-final.log/xml` 均保留在 `tmp/rag-verification-20261118/tool-validation/`；该项隔离复验 1/1 通过。此时不能声明全套通过，也不能仅凭单测通过认定已定位所有原因。生产 reranker 与该既有测试未修改，后续同进程组合和排除新文件的基线核对另列结果。

截至 B 初始提交 `f812751ae0b487de2bb269597747c58d867a2430`，本包定向 56/56、新两文件后接既有 reranker 同进程组合 94/94 通过；两次全套分别为 1712 通过/1 失败/25 跳过及 1714 通过/1 失败/25 跳过，期间新增测试使总数不同。主 Agent 独立排除两新文件复核原有范围，1663 通过/25 跳过、2 警告，退出 0，见 `baseline-existing.log/xml`。这仍不证明新增测试与重复超时无关，客户端生命周期及全套负载下的时序边界由独立质量审查继续核对。未改生产超时、旧测试或通过分母。

### B 工具审查、收尾修复与最终离线验证

独立规格审查要求保存已校验的实际 `document-map.json` 副本，现与 mappingSha256 一起冻结。独立质量审查定位两项评测工具缺陷：先创建基类默认模型客户端后覆盖导致首个客户端未关闭；最终 save 或 ledger.close 抛错可跳过其余资源清理。提交 `0d5748cd758ee1c81ddfde61feba1e105a2439e3` 改为唯一零重试客户端，并将报告写盘、账本、逐资源和 GPU release 分别保护，原取消继续传播。先红后绿的注入回归及首次失败日志完整保留；关闭失败只记录固定标签，不宣称已强制清理。

修复后定向 **61/61**、新两文件后接既有 reranker 同进程 **99/99**，全套 **1724 通过、25 跳过、0 失败/错误、2 警告**，共 1749 项，退出 0。compileall、`uv lock --check` 及离线带映射入口通过，见 `tool-validation/review-fixes-*`。本次通过不构成此前 30ms 超时根因已查明的证明；旧测试与生产超时均未改。最终规格与独立质量审查通过，质量审查另独立运行清理注入 5/5。

主 Agent 又在启动前完成零 HTTP 配置预检：供应商地址可解析、凭据已配置，按相同参数构造 Embedding 与唯一回答 SDK 客户端并关闭，0 请求/0 调用，授权锚点仍不存在，见 `sdk-preflight.json`；它不证明远程凭据有效或网络可达。按既有评测说明，将本任务产物目录 ACL 核对并限制为当前评审用户、SYSTEM 与 Administrators，未修改通用 tmp 的权限。正式 run 保存实际最终 contexts/messages、raw response、usage、候选排序及计时，原始产物不提交。

### B 唯一真实首轮与成本

在 `ai-service` 执行：

```powershell
uv run python -m ai_service.orchestration.rag_rerank_experiment --live --document-map ../tmp/product-document-map.json --output ../tmp/rag-verification-20261118/live
uv run python -m ai_service.orchestration.rag_blind_review --report ../tmp/rag-verification-20261118/live/report.json --export ../tmp/rag-verification-20261118/blind
```

本轮固定授权已用完，不能重复这条 live 命令、删除锚点或换目录再跑。北京时间 15:14:56–15:19:04，runner 完成，墙钟 248.108 秒。实际 **180 次回答**，其中 176 正常解析、4 解析失败，无未运行项；关闭组 87 正常/3 故障，开启组 89 正常/1 故障。查询 Embedding 仅 3 请求、32/32/26 输入，共 90 输入。账本含 1 授权、3 Embedding 预占、180 回答预占，累计 184 行；独立核对实际模型调用标志 180，结束后 Spring snapshot 与启动时相同，epoch 1。没有自动重试、额外裁判、稳定性轮次或专项真实调用。

冻结仍为 11 文档/72 块、3072 维 `openai:text-embedding-3-large`，Milvus AUTOINDEX/COSINE、Strong consistency。模型 `deepseek-chat`，temperature 0.1、max_tokens 8192、提示预算 16384 字节。BGE 固定 revision `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`，RTX 4050 Laptop GPU（总显存 6438780928 字节）、fp16、torch 2.6.0+cu124；模型及 tokenizer 文件哈希在真实报告中。代码 SHA 为 `0d5748c`；dirty 指纹仅包含本轮尚未提交的说明/计划和用户既有 README 格式及取消计划，不含未提交实验源码。

| 本轮观测 | 关闭重排 | 开启重排 |
|---|---:|---:|
| 回答输入 tokens | 64063 | 64464 |
| 回答输出 tokens | 6255 | 6732 |
| 回答总 tokens | 70318 | 71196 |
| 回答供应商耗时 median / p95，ms | 1003.522 / 1421.857 | 1015.139 / 1598.178 |

全部 180 响应都有 usage，含解析失败，共 141514 回答 tokens；其中 cache-read 54400，查询 Embedding 另计 1693 tokens。未核实本轮供应商计费单价，货币金额保留 null。共享检索 median/p95 为 32.990/44.531ms；90 次真实 BGE 重排为 125.263/144.663ms，总 11.418 秒，另有一次本地预热 1.280 秒。它们与回答分开计时，不是两次完整在线 E2E。最终上下文集合 62/90 对不同、选择或顺序 76/90 对不同；输入改变不是答案质量证明。

原始报告、规范、实际映射、行与向量、盲包及账本均在 `tmp/rag-verification-20261118/`。报告指纹 `ad21903b50000f50d077af1c93b137e77bff1da6ca47d4ca04617f8c33781ee1`；规范行及 rubric 指纹与前置预检一致。收尾 `cleanupErrors` / `reportSaveFailures` 均为空。随后按启动时间和命令身份核对，只停止本轮自启 Spring 进程树，恢复启动前停止状态；Docker/Redis 保留，Python API 未启动，未改配置文件。

### 全量代理盲审：原冻结标准的初审结果

180 张卡随机匿名分为三份，每份 60，由三个独立代理逐事实、逐可核验断言、逐引用语义初审。审阅者只读取自己的公共卡与 schema，不接触 arm、分数、执行顺序或私有映射。所有结果冻结后，主 Agent 合并并调用 `validate_reviews` 校验完整 180 ID、314 必要事实判定、逐字答案/实际上下文引文及引用关联，才解盲统计。机械校验不能证明语义判断正确或断言穷尽；这些仍是代理初审。

| 原冻结标准、每组 90 题 | 关闭重排 | 开启重排 |
|---|---:|---:|
| 全答案通过 | 63/90（70.00%） | 74/90（82.22%） |
| 可答题通过 | 54/81（66.67%） | 65/81（80.25%） |
| 正确且安全的契约拒答 | 9/9 | 9/9 |
| 正常响应中的误拒答 | 8 | 3 |
| 公共必要事实满足 | 95/130（73.08%） | 105/130（80.77%） |
| 无依据断言 / 已审断言 | 7/327 | 1/362 |
| 有支持引用 / 已审引用 | 102/104 | 119/119 |

配对四格：两组都通过 61、仅关闭通过 2、仅开启通过 13、都失败 14。开启减关闭为 +12.22 个百分点；题级配对 bootstrap 10000 次、seed 20261118，95% 区间为 [+4.44，+20.00] 个百分点。这是反复使用的 90 题开发回归集、每组每题一次、原冻结标注下的代理估计，不能当独立泛化或人工最终准确率。4 个解析故障留在端到端分母；两项关闭组独有解析故障也影响配对差，不能把全部差额确定归因于排序。

`reviews-agent.json` 指纹 `d08d83fd4ff7d1b91522f14af83f317864459ec699caa53652c6f7ffbb5e38d0`，原始审阅及 provenance 保留。用户明确选择“先完成代理初审，人工验收保留待办”。`blind/cards.md` 为完整材料，`blind/reviews-human.json` 为全部 pending 的独立人工副本；其导入结果 `analysis-human-pending.json` 不产生人工总体改善结论。

### 评分后失败归因与支付口径澄清

对原初审未通过的 43 张卡另做评分后归因，提供完整同次 Top8、排序、选择丢弃原因及最终实际文本，保持 arm 隐藏、冻结评分不改。主 Agent 再逐项校验 60 个未满足事实的归属及支持摘录。下表按**原冻结标注**计数，不是最新产品口径的人工裁决：

| 未满足公共事实的机制 | 关闭重排 | 开启重排 |
|---|---:|---:|
| 同文档单块配额丢失 | 21 | 15 |
| Top8 没有必要语义 | 6 | 6 |
| 最终证据已在场但生成遗漏 | 3 | 3 |
| 由请求解析故障造成，单列 | 5 | 1 |

前 3 类共 54 项，其中 36 项为配额损失；另有 6 项事实因 4 个请求故障失去可用答案。按卡统计类别可以重叠：关闭组配额 13 卡/未召回 4 卡/生成问题 8 卡，开启组 8/4/4 卡；生成问题还包括无依据补充或冗余引用。未观察到单独的异文档排序截断或字符/字节预算损失，不能据此保证以后不会出现。4 个解析失败均为 `citedChunkIds` 键缺开头双引号，离线 `json.loads` 明确报错，原自然语言常已有正确内容；生产仍严格拒绝坏 JSON，不手工修复后算成功。

主 Agent 还检查了 15 对不一致通过结果及 23 对完全相同答案。相同答案没有公共事实判定的机械冲突，但支付题 `boundaries-safety-001` 发现必要事实要求服务端/权限/外部服务、等价表达只要求后端/接口的完整度歧义。用户在看到该疑义后明确确认：**“没有支付功能 直接说现在不支持支付功能”**。因此后续直接答案应为“目前平台不支持支付功能。”，不再要求完整后端核验清单作为该题必要回答。

该澄清发生在生成及初审冻结之后，记录为 `evaluation/payment-clarification-20261118-v1.json`，没有静改旧问题、语料、rubric 或原始评分。两组旧答案都只说明“按钮不证明接入”，没有直接说明当前不支持；将本题两组均按新口径记未满足、其余 89 对保留原判的**后置敏感性复核**为关闭 62/90、开启 74/90，四格 61/1/13/15。这不是新一轮模型验收或原冻结指标。澄清文件含可发布的短知识候选，本轮尚未写入 Spring/Milvus、未额外调用 Embedding；当前知识库仍是上述冻结版本。单个产品事实获得确认不代表 180 份答案已人工验收。

### 交付与下一轮建议

脱敏聚合为 `doc/knowledge/product/evaluation/results/rerank-20261118-v1.json`，方法与入口已同步两份 README。本轮没有改生产 R1、阈值、Top8 或配额；生产继续使用现有开启重排、cap1 配置。当前数据支持保留重排，并优先处理同文档证据损失，但正式质量结论保持人工待办。

下一轮推荐先用本轮冻结候选、query 向量与 BGE 分数，单独做 cap1/cap2 的离线证据选择对照，保持最终三块及字符/字节预算，逐事实核对能挽回哪些遗漏。理由是配额造成的必要事实损失最多；代价是同文档多块可能挤掉其他文档、仍不能解决 Top8 未召回、模型遗漏或坏 JSON。若要正式上线，还需同步 Spring/Python/前端多块来源契约及相应验证，不能只改 Python 常量。

支付新口径须先用独立语料版本同步文档和标注，再安排新 INDEX/发布及定向答案验证；不能与 cap 配额变化混作本轮单变量收益。JSON 输出模式也可另作单变量候选，以降低已观察的键名引号错误；需要先确认当前供应商能力，保留严格解析、原失败和无重试边界。任何新 INDEX Embedding、回答对照、稳定性补验或付费裁判都须另列额度，本轮不扩大已经用完的授权。8 道 SLA/错误前提/部分可答/条件不匹配专项仅完成离线定义，真实专项与全量人工验收仍待后续。

## 第十三轮：固定开启重排的 cap1/cap2 离线证据验证（2026-11-18）

### 问题与方案取舍

用户在第十二轮交付后授权“继续执行下一步验证”。上轮 54 项非请求故障造成的必要事实遗漏中，36 项归因为同文档单块配额损失；本轮先验证已建议的 cap1/cap2 证据选择，不改生产行为、不再消耗已用完的真实调用授权。扩大 TopK、换提示、改阈值或同时更新支付语料会混入其他变量，因此本轮均不采用。

输入是第十二轮唯一真实首轮留下的实际 Milvus Top8、固定 BGE 顺序、11 文档/72 块语料和查询向量，不重新检索或重排。维持开启重排、最终最多三块、每块 1200 字符、总计 3000 字符、JSON 16384 字节，仅将单文档配额由 1 改为 2。预读原 draft-2 核对身份，事实字段不参与选择；完成选择后才投影事实供独立语义审阅。实施与判据预先记录在 `docs/superpowers/plans/2026-11-18-rag-cap-evidence-verification.md`。

### 流程、模块与可追溯边界

`tmp/rag-cap-evidence-20261118/replay.py` 校验原报告规范化指纹、语料/检索冻结/映射、题目 ID 绑定的查询向量指纹及十项输入文件字节摘要。复用 `rag_evaluation_workflow.select_contexts` 只改变配额，并直接调用正式 `_filter_and_dedupe`、`_validate_ranked`、`_select_documents`、`_bounded_contexts` 和 `fit_customer_service_contexts` 核对过滤、排序、cap1 与预算。cap1 的 90 组最终上下文与原开启组逐字一致，包括原解析故障题的实际输入。

回放脚本安装网络审计拒绝钩子，没有构造供应商客户端、加载 BGE 或连接 Spring/Milvus；网络尝试为零。旧原始报告、语料、rubric、答案、审阅和授权账本前后字节摘要不变。查询向量只做来源完整性核对，不重算 dense 召回，也不将实际 Milvus 候选改成内存精确检索。

两组最终上下文对每题匿名为 X/Y，顺序固定随机化，不给审阅者 cap、分数、旧答案、旧判分或未入选正文。三名独立代理各 30 题，分别审阅 42/43/45 项公共事实的两组输入，共 **260 项语义判定**。`covered` 要求全部必要要素及对象/适用条件有依据，`partial` 表示仅有部分支持；逐字引文只验证溯源，不替代语义判定。9 道拒答题的上下文仍回放，但不据此生成新的拒答成绩。全部 180 个组别记录、260 个 factId/判定及 270 条支持引文通过严格导入后才解盲，12 项错 ID、缺事实、伪造引文、pending 注入均被拒绝。

### 本轮离线结果：证据覆盖，不是答案通过率

匿名初审原始汇总为完整支持 105/130 对 114/130，原三份 review 及 `initial-summary.json` 保留。独立质量审查发现 `admin-knowledge-005:required:02` 和 `deployment-download-009:required:02` 将适用范围、等价表达中的限制追加成额外必述事实，导致直接支持 statement 的上下文被过严判为 partial。原评审者保持匿名身份，对两事实两组做定向复核：一条 missing 维持、三条 partial 改 covered。`audit-corrections.json` 独立记录四条原判/复核、逐字证据与理由，严格核对原判、身份、实际上下文及两份定向复核文件后才形成下表审后结果；6 项原判错配、伪造引文、未绑定理由/判定、复核哈希错配及缺组注入均被拒绝。未改原 rubric 或静默覆盖盲审。适用范围仍约束结论，不据此推断物理清除完成、任意用户下载或具体登录规则。

| 全部 81 道可答题、130 项公共必要事实 | cap1 | cap2 |
|---|---:|---:|
| 完整支持的必要事实 | 106/130 | 116/130 |
| 部分支持 | 12 | 7 |
| 无支持 | 12 | 7 |
| 题内全部必要事实均有完整证据 | 67/81 | 72/81 |
| 字符裁剪题数 / 字节裁剪题数 | 0 / 0 | 0 / 0 |
| 最大 JSON 输入字节 | 1642 | 1646 |

审后事实转移为完整→完整 106、部分→完整 6、无→完整 4、无→部分 1、部分→部分 6、无→无 7。新增完整覆盖 **10 项**，本轮代理判定未观察到已完整支持事实退步；完整证据配对为两组都完整 67、仅 cap1 完整 0、仅 cap2 完整 5、两组均不完整 9，与原盲审相同。没有新增生成答案，不能将 72/81 称为回答正确率，也不能与第十二轮 63/90 对 74/90 的重排开关答案成绩合并。

65/90 题选块改变，累计新增 65 块、丢失 59 块/59 个文档出现次数；没有引入新文档，另 6 题由原来不足三块补入同文档块。cap1 文档数为三篇 84 题、两篇 6 题；cap2 为三篇 25 题、两篇 65 题。虽然此集未观察到公共必要事实覆盖退步，文档多样性下降是实际代价，不能保证新问题没有跨文档损失。

新增完整证据主要涉及首次使用顺序、MULTI_FILE 结构、待审帖流程/访问权限、求助模板和知识变更异步流程。上轮开启组 15 项 quota 归因中，本轮 cap2 审后完整支持 8、部分支持 2、无支持 5；其中只有 **7 项是 cap1→cap2 新增完整支持**。`deployment-download-013:required:02` 本轮两组均判完整：cap1 已说明 Vue 构建产物缺失可能使部署失败，并有核对构建结果的语义。它与上轮该项 quota 归因存在审阅差异，本轮明确单列，不改历史归因，也不将其计入 cap2 收益。

cap2 审后仍有 14 项未完整支持，分布在 9 题。典型缺口是三种类型比较只补入 MULTI_FILE，HTML/Vue 关键说明仍缺；上传限制题的第二块仍是发布规范，尚未选到格式/20MiB/有效性正文；禁用题补入异步任务说明，但不足以证明物理清除边界；退款现状和下载免部署前置也不能从一般条件性规范直接推出。这说明 cap2 有限缓解配额损失，不能代替召回、相关性排序、知识口径和生成契约的后续验证。

### 本轮命令、失败记录与部署状态

在仓库根目录执行临时 `replay.py` 和 `summarize.py`，原始回放、匿名包、三份审阅、支持引文、变更审计与指纹保存在 `tmp/rag-cap-evidence-20261118/`。在 `ai-service` 执行：

```powershell
uv run pytest tests/test_rag_bounded_evidence_experiment.py -q --junitxml=../tmp/rag-cap-evidence-20261118/selection-tests.xml
```

本轮组件测试 **25 通过、0 失败/错误/跳过**，2 条依赖弃用警告；90/90 基线精确回放及全部预算断言通过。初版临时脚本将向量矩阵指纹误与 caseId→vector 映射指纹比较，在回放前断言失败；核对原 runner 的真实生成方式后修正校验，保留 `replay-attempt-1.json`，未改源向量，也不是生产缺陷。

本轮新增聊天、Embedding、BGE 推理、INDEX、生产写入均为 **0**，未启动服务、未重跑完整 Python/Java 或浏览器。第十二轮 1724 通过/25 跳过等记录仍属于上一轮，不能当成本轮全套验证。支付澄清仍为独立、未发布候选，旧事实标准未改，人工验收仍待办。

### 来源契约核对与下一轮

本轮额外只读核对源码：Python 响应 schema、Spring `CustomerServiceAiClient` 和 `CustomerServiceAnswerServiceImpl` 已用最多三项来源数组及 chunkId 表达和校验来源；Spring 按 chunkId 去重，前端已提交 HEAD 的规范化器也按 chunkId 去重，来源组件以 chunkId 为 key。当前 wire 可表达同文档不同块，并不能预先认定三端都必须改 schema；生产 `_select_documents` 仍按 documentId 只保留一块。该结论只是源码核对，尚无本轮多块跨服务/浏览器验收。前端三个既有修改与后端 README/取消计划均保留。

下一轮建议围绕单一 cap2 候选先实现默认保持 cap1、可回退的配额开关，补同文档两块的引用身份/版本/顺序与三来源上限测试，按上述真实契约决定最小变更范围。随后在另行列明的回答额度内进行 cap1/cap2 真实答案对照，验证本轮新增证据是否被模型使用、文档多样性降低是否造成退步。语料与支付新口径另走独立版本和 INDEX 验证，不与配额收益混合；未召回、同文档第三块、物理删除措辞缺失、模型遗漏和坏 JSON 仍可能失败。

独立规格/方法审查及语义/结论质量审查通过，审查记录、原盲审与审后侧车均保留。本轮仅提交脱敏聚合、方法计划及交接说明，未部署生产配置；提交哈希以本轮交付记录为准。

## 第十四轮：可回退的单文档块配额与三端来源验证（2026-11-18）

### 问题、历史依据与方案取舍

用户在第十三轮交付后要求“继续执行下一轮优化”，本轮落实上轮已明确建议的默认 cap1、可选 cap2、可回退路线。同文档所需事实分散在不同块时，旧选择器只保留其中一块。第十三轮审后 106/130 对 116/130 的完整事实支持是历史离线证据，支持开展有界候选实现；它不是本轮新答案通过率，也不构成人工验收。

直接默认改成两块缺少独立回退入口，按题自适应配额会增加策略变量。本轮采用数值配置 `AI_SERVICE_RAG_DOCUMENT_CHUNK_LIMIT`，默认 1、仅允许整数 1/2，环境字符串 `1`/`2` 可读，布尔、浮点及范围外值拒绝启动。保持现有 Top8、重排器、阈值、提示、语料及最终三块预算，不更新支付语料，不沿用耗尽的首轮真实调用授权。

### 触发、判断、流程与关键模块

`Settings.rag_document_chunk_limit` 在 `CustomerServiceRagService` 创建时固定；活动版本过滤、chunkId 去重、稳定重排及原阈值之后，`_select_documents(..., per_document_limit=...)` 按当前顺序分配名额。设为 2 时，同文档第二块也占总名额，第三块不能进入，其他文档可在余位补入；最终仍最多 `min(rag_final_top_k, 3)` 块。禁用重排及故障降级使用原候选顺序和同一配额，有阈值的故障继续失败关闭。

默认 cap1 的选择及主要诊断保持兼容：同文档超额为 `duplicate_document`；cap2 超额为 `document_chunk_limit`；总名额耗尽为 `top_k`。`customer_service_trace` 的固定 `configFingerprint` 增加 `documentChunkLimit`，没有新增问题、正文或答案日志。字符 1200/块、3000 总量及实际 UTF-8 JSON 字节预算仍在选择后执行；引用仍只能来自最终上下文中的唯一 chunkId，按模型引用顺序返回各自身份、locator 和摘要。

旧 `rag_rerank_experiment` 与 `rag_snapshot_experiment` 的真实入口固定 cap1，在供应商、输出目录及授权账本初始化前拒绝 cap2；补了禁止触达资源的入口测试。历史固定上下文拒答实验不经过选块，不作改动。`.env.example` 和 Python README 说明默认、启动生效与回退；没有更换 wire/schema。

### 本轮离线验证与失败记录

原 Python 四文件定向基线 **168/168**，Java 原两类 **20/20**，前端当前工作树原客服测试 **30/30**。Python 新增 29 项行为测试，先红 **197 项中 23 失败**，实现后 **197/197**；覆盖稳定排序、第三块/总名额、默认及环境校验、禁用/降级、阈值、字符/实际 JSON 字节、旧版本/不活动/重复块、逆序/重复/未知引用和旧实验保护。独立规格与质量审查通过，质量审查另跑新增 **29/29**。

正式回放脚本为本机忽略目录 `tmp/rag-cap-rollout-20261118/formal-replay.py`。它读取第十二轮真实报告冻结的实际候选、查询向量和 BGE 顺序，校验报告/rubric/检索/向量及原文件摘要，经 `rag_rerank_experiment.answer_once` 调用正式 `CustomerServiceRagService`。cap1、cap2 各 **90/90** 组最终上下文与第十三轮对应输入逐字一致，**180/180** 组逆序引用身份、定位和摘要匹配。这里的 **180 次是 Fake Answer Model 调用**，输出固定文本，只验证选择/预算/来源协议；不能用作新答案质量、拒答准确率或跨服务在线 E2E 成绩。

首试临时回放脚本在任何 RAG 调用前被审计钩子拦截：Windows asyncio 初始化的本机 socketpair 也产生 `socket.connect`。核对标准库调用栈后，仅将本机事件循环建立移至审计之前；全部服务导入与 RAG 执行仍禁止 connect/getaddrinfo/sendto，成功运行该阶段网络尝试为 0。首试日志 `formal-replay-attempt-1.log` 保留。这是本轮验证设施初始化问题，没有修改生产网络或引用逻辑。

在 `ai-service` 执行 `uv run python -m compileall -q src`、`uv lock --check`，均退出 0；完整 `uv run pytest --junitxml=../tmp/rag-cap-rollout-20261118/python-full.xml` 为 **1753 通过、25 跳过、0 失败/错误、2 条依赖弃用警告**，退出 0。第十二轮 1724 通过等是历史记录，本轮没有用其替代完整验证；本轮全套也不证明历史 30ms 恢复调用超时根因已经查明。

Java 新增独立 `CustomerServiceMultiChunkSourcesTest`，本地 `HttpServer` 回显真实 requestId，串联真实 `CustomerServiceAiClient` 和 `CustomerServiceAnswerServiceImpl`，仅 Mapper 使用替身。`7:2:1, 7:2:0, 8:3:0` 保留同文档两块及另一文档的来源顺序和全部元数据；九种第二来源异常拒绝整次回答；超过三项在 HTTP 客户端解析边界拒绝，服务不查 Mapper。状态异常用第二文档，首文档保持合法，并核对读取顺序，避免首项提前失败的空覆盖。最终覆盖审查又补上同文档第二块的旧版本、错名称、chunk 文档身份错、版本身份错和索引越界五项；首块合法、两次读取相同文档并整次拒绝，可防止未来按文档跳过第二块校验的回归。它不证明数据库原子快照或真实 Python 服务。

在根目录执行 `mvn clean -DskipTests compile`，退出 0；随后执行 `mvn "-Dtest=CustomerServiceMultiChunkSourcesTest,CustomerServiceAnswerServiceTest,CustomerServiceAiClientTest" test`，初版 **31 通过**、新增 11；补齐上述五项后主 Agent 同命令复验为 **36 通过、0 失败/错误/跳过**，本轮新增合计 **16**。原 `java-final.log`/XML 保留 31 项证据，补强日志 `java-review-fix.log` 与最终 `java-final-v2.log`/三份 XML 单列。没有修改生产 Java 或协议 schema，测试补强后未重复生产干净编译。

独立前端仓库仅新增 `tests/customerServiceSources.test.ts`，**4** 项覆盖同文档双块及第三文档的顺序/身份/独立 locator/excerpt、按 chunkId 去重、第四来源不能超过三项、成功状态的三个公开来源和空白/Unicode 摘要边界。`normalizeCustomerServiceSources` 函数与已提交 HEAD 相同，未修改组件、生产函数或来源 schema；用户原三项修改仍在工作树。

前端执行两份客服测试 **34/34**；独立质量审查提出期望对象与输入别名可能使未来原地修改误绿，随后四处调用前建立独立快照，复验仍 **34/34**，原目标日志保留、修正日志单列为 `frontend-review-fix.log`。主 Agent 在修正后合并执行以下五文件为 **48 通过、0 失败/取消/跳过**，修正前 48 项日志另存。`npm run type-check` 和 `npm run build-only` 同轮退出 0，构建保留动态/静态混合导入及大于 500 kB 包体积提示。它们来自保留三项用户改动的当前工作树，不能称为干净前端 HEAD 或浏览器交互验收。

```powershell
# 在独立前端仓库执行
node --test --experimental-strip-types tests/customerServiceSources.test.ts tests/customerService.test.ts tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts
npm run type-check
npm run build-only
```

### 部署状态、边界与下一轮

配置需要重启进程生效，改回 1 并重启可回退。本轮不修改真实 `.env`，不启动或重启 Spring/Python 正式服务，不启用本地或生产 cap2；新增真实聊天、Embedding、BGE 推理、INDEX 和生产写入均为 **0**。原真实调用账本保持耗尽状态，不能删除、更换目录或重建账本再跑。

原 `.env`、184 行授权账本、历史真实报告、取消计划及前端三项已有修改的字节摘要均与本轮初始快照一致；正式回放全部输入摘要也保持一致。README 只隔离暂存本轮配置说明，原三个反引号改为两个的用户 hunk 继续未暂存。核对证据为 `final-controls.json`，原始产物全部留在忽略目录。实现提交为后端 `6822752`（Python 配额）、`84d34f0`（Java 测试）及 `54cc728`（同文档第二块负向补强），前端独立提交 `2aa44fb`（来源测试）与 `16b6074`（期望快照加固）；设计/计划为 `81b2195`。脱敏聚合为 `doc/knowledge/product/evaluation/results/cap-rollout-20261118-v1.json`，旧 31 项聚合草稿另存在本机忽略目录，本轮交接说明提交以最终交付哈希为准。

cap2 可能挤掉其他文档，也不能解决 Top8 未召回、同文档第三块、知识缺失、模型遗漏或坏 JSON。正式路径精确回放及来源契约通过不代表真实回答质量已通过；用户指定的全量人工验收继续待办，支付“目前平台不支持支付功能”仍是单列未发布候选，不与配额收益混合。

下一轮应单列 cap1/cap2 真实回答额度与新实验入口，保持冻结候选、BGE 顺序、提示和预算，仅改变文档配额，验证新增证据是否被使用以及跨文档退步。失败、坏 JSON 和未运行仍留在每组分母，代理审阅与人工裁决分开；未获新额度前可以准备设施及离线校验，不能启动真实答案对照。支付语料和 INDEX 另走独立版本及授权，不同时加入这一变量。

## 第十五轮：固定输入的 cap1/cap2 答案对照设施（2026-11-18）

### 问题、方案与授权范围

用户在第十四轮交付后要求“继续下一轮优化”，本轮落实已确认的固定输入路线，先交付实验设施及离线验收。第十三轮 cap2 增加证据覆盖、第十四轮配额开关及三端来源验证是历史依据，均不是本轮新增答案质量成绩。两组各重新回答 90 题，可减少复用旧 cap1 答案带来的供应商时间差；重新召回或同时修改支付知识会增加变量，因此本轮复用历史输入，不修改生产 cap1。

本轮设施准备不包含新的真实调用额度。旧重排实验的 180 次回答及 90 个 query Embedding 输入已经耗尽；旧账本、报告和生产 `.env` 保持。新实验需在设施、完整计划与请求可审阅后，单独确认最多 180 次新回答，新增 Embedding、BGE、INDEX 为零；未收到明确答复不得创建正式锚点或发送真实请求。人工验收按用户安排继续保留待办。

### 输入、正式路径与批准绑定

新增 `rag_cap_contracts.py` 校验第十二轮历史报告规范化指纹、原 rubric、实际文档映射、11 文档/72 块规范行、90 题 ID→向量和完整检索/排序身份。向量源文件是题序矩阵，校验必须重建 ID 映射，不能直接比较矩阵 hash。历史候选的身份、正文、分数及活动版本元数据经完整核对，再用正式 `CustomerServiceRagService` 和旧 `answer_once` 回放；两组均使用冻结开启重排顺序，唯一变化为单文档配额 1/2。

`rag_cap_experiment` 默认离线；`--prepare` 在新 tmp 目录生成 `plan.json` 与 `decision=pending` 请求。预备阶段两组各 90 个最终上下文与第十三轮逐字核对，绑定输入、配置、提示、关键执行文件、输出位置及题序；原标注和支付澄清分别绑定摘要，不静默覆盖冻结标准。`--live --plan ... --approval ...` 严格验证 approved、study、计划 hash、输出及整数 180；额外字段、布尔额度和任何相关漂移均在 SDK/账本前拒绝。HEAD 仅诊断，无关文档提交不使计划失效。批准文件只证明结构与绑定，不能认证批准人，Agent 必须等可信用户明确确认后才可创建正式 approved 文件。

模型包装层发送前再次核对完整问题及实际 chunkId/content，正式选择、字符/JSON 字节预算、严格 JSON 和引用检查继续复用原路径。本实验只读历史 publication，不访问当前 Spring/MySQL/Milvus，不声称当前权威快照复核或在线 E2E。报告保存源 snapshot/receipt、准备与运行实现、计划/批准指纹、实际消息/原始响应、usage、结束原因及耗时，原始产物只留本机忽略目录。

### 额度、故障与盲审边界

新 `CapAuthorizationBudget` 固定 study `rag-cap-20261118-answer-round-v1`，锚点 `tmp/rag-cap-answer-20261118/cap-authorization.jsonl` 独占创建；不提供 Embedding、退款、恢复或续跑接口。每次发送前 flush/fsync 持久预占，未知/重复 key、超过 180、已有锚点或持久化不确定均拒绝。失败、取消和进程退出均占额度，SDK 零重试；发送前故障停止，连续三次实际供应商调用错误也停止。

运行前保存全部 180 个 not_run 占位，题内交替 cap1/cap2、单并发、逐条保存；错误与未运行留在每组 90 分母。取消保存当前错误并传播原取消；报告写盘或一个 closer 失败仍尝试其他资源，不能声称故障资源已强制关闭。旧 `_finish_run` 仅增加带兼容默认值的写盘错误码参数，旧真实入口及旧账本不重构。

`rag_blind_review` 使用固定双 study 注册表；旧 study 只接受 rerank_off/rerank_on，新 study 只接受 cap1/cap2，报告/公共包/私有映射/评审同 study 绑定。旧分析键保持，新分析使用 cap1Only/cap2Only/cap2MinusCap1。公共包投影实际问题、事实、contexts、答案及来源，隐藏组别、配额、分数、usage、耗时与执行顺序；全 180 项包括故障均保留。pending 不产生质量提升值，完整代理评审也不能转成人工完成。

### 本轮验证与交付状态

旧重排/盲审工具定向基线为 **63/63**。任务一先保留两项行为红测（2/2 失败），初次扩展运行 62 项中一个排序预期错误：整表逆序仍满足独立账本的配对交替约束，具体题序由计划绑定；修正测试并保留正向用例后，初版定向 **89/89**。根 Agent 使用历史冻结文件的离线预检为 cap1/cap2 各 **90/90** 最终上下文逐字一致、网络尝试零，未创建正式锚点。

独立规格审查虽复跑 89 项全绿，仍发现三项遗漏：正式检索回放在模型前抛异常时，服务将其折为 error，而运行器会扫描完 180 项并标 complete；关键摘要漏了最终提示导出与预算 helper；未进入 Top8 的规范行缺少完整身份检查。审查最小复现与初版证据保留，不将测试绿等同规格通过。修正增加 **9 项先红后绿**回归，随后定向 **98/98**；模型前 error 立即保留当前错误及其余 not_run，`prompts/__init__.py` 和 `models/input_review.py` 纳入漂移绑定，全部 72 块在新 loader 内按正式 revision-v2 公式核对行 ID、canonical chunkId 及 snapshot 元数据。行 ID 是正式身份散列，不能简单等同 chunkId；旧生产语料校验模块未改。

规格复审独立 **98/98** 并重做三个负例后通过。质量审查又确认新夹具在隔离前调用旧真实 `load_rubric`，导致 Git LF 语义下被本机历史 CRLF 字节锁阻断；只将新夹具改为读取公开 rubric 结构并生成隔离来源，仍真实执行 cap loader，没有放宽任何旧 freeze 或 pin。该问题先红 1 项（同次另 3 项通过），修正后定向 **101/101**；独立 LF 语义探针可生成 90 题/72 块，旧真实校验仍按原设计拒绝。上下文守卫原两负例复用失效状态的弱覆盖也改为独立实例，并另测 sticky；删除问题/正文比较或 sticky 的三个内存变体均被测试拒绝。没有据此声称实际运行了 Linux 全套测试。

任务二初版定向 **64/64**（旧 28、新 36），红测 64 项中 5 失败、19 错误保留。独立规格审查发现 `sources` 原样透传可夹带组别、配额及耗时；补正式六字段纯标量白名单及长度/版本校验，两个 study 的 JSON/Markdown 防泄露、旧 chunk_id-only 兼容、原报告不变均验证。新增负例先红 **98 项中 30 失败**，修正后 **98/98**；规格复审独立 98 全绿，并另做 14 次导出和 54 个非法来源拒绝。初版 `237f3c7`、来源修正 `ef32591`，旧 64 项测试与原记录均保留。

根 Agent 的完整离线运行经真实 `_runtime_model` 与 Fake ChatOpenAI SDK，**180 次 Fake SDK 发送**按题交替、两组各 90；每次发送时已经存在持久预占，全部上下文逐字匹配计划，180 组逆序引用及身份/版本/定位/摘要核对通过，两个 SDK 客户端均关闭。随后用同一报告导出 180 张完整匿名卡并严格导入 pending，`paired=null`、人工总体改善值 null；来源修正前后包分别保留为 `blind`/`blind-final`。这不是 180 份真实新答案或代理语义初审，未评价新答案质量。

网络审计从 Windows 本机 asyncio 唤醒通道初始化后开始，覆盖服务导入及全部运行器工作，网络尝试为 **0**；匿名包阶段另做零网络核对。Fake 账本与批准文件仅在 `offline-simulation` 隔离路径使用，正式锚点不存在，未用此文件授予实际额度。原始脚本/报告/包和所有红绿记录留在 `tmp/rag-cap-answer-20261118/`，脱敏汇总为 [cap-answer-facility-20261118-v1.json](knowledge/product/evaluation/results/cap-answer-facility-20261118-v1.json)。

在 `ai-service` 执行以下本轮检查：

```powershell
uv run python -m compileall -q src
uv run pytest --junitxml=../tmp/rag-cap-answer-20261118/tool-validation/python-full-final.xml
uv lock --check
```

compileall 与锁检查退出 0；初版全套 **1855 通过/25 跳过**，来源修正后的最终全套 **1889 通过/25 跳过、0 失败/错误、2 条依赖弃用警告**，退出 0。初版日志 `python-full.*` 与最终 `python-full-final.*` 独立保留；没有用第十四轮 1753 通过或 Java/前端成绩替代本轮，也未重跑 Java/前端、启动正式服务或做浏览器/当前双服务 E2E。

首次基线日志目录配置在 pytest 启动前被 PowerShell 重定向拒绝，修正路径后才执行上述 63 项；说明保存在 `setup-note.txt`，不是代码测试失败。保护脚本首试用 Windows 默认 GBK 解码 Git 的 UTF-8 中文上下文失败，在生成保护记录前退出；仅将临时脚本显式设为 UTF-8 后通过，记录 `controls-attempt-1.txt` 保留。任务一初版提交 `0636fb2`、规格修正 `286d073`、测试质量补强 `6fa3ae1`，设计与计划 `1774489`。新真实聊天、Embedding、BGE 推理、INDEX 和生产写入均为 **0**；cap2 未部署。最终保护记录 `final-controls.json` 确认原 `.env`、旧 184 行账本、报告、两轮回放、取消计划与前端三项用户修改共九份文件字节摘要未变，README 原围栏 hunk 隔离保留。

两任务的独立规格/质量复审及最终交付审查均通过，原审查问题和红绿证据全部保留。具体新计划与 pending 请求将在本次交付提交后生成，不能据设施验收取得真实调用授权。

### 下一轮

在单独获得具体新回答额度后，用最终计划执行两组各 90 次真实回答，再对全 180 份匿名答案做独立代理初审和配对分析；人工产品验收继续待办。故障、坏 JSON、未运行、跨文档退步及原标注/支付新口径复核均须分开报告。cap2 未经新答案对照不能升级为默认；支付语料、INDEX 和额外付费裁判另走独立版本及额度。

## 第十六轮：cap1/cap2 真实回答及全量代理初审（2026-11-18）

### 问题、证据与方案取舍

第十三轮仅证明固定上下文的证据覆盖发生变化，第十四、十五轮提供配额开关、来源契约和答案实验设施，均不能证明新增证据被真实答案使用。用户在第十五轮提交 `346c26e` 后明确答复“批准本轮最多180次真实回答”，本轮据此执行已交付的固定计划，再完成全部答案代理初审。第十五轮当时的零真实调用和待批准记录保留为历史，不用本轮状态覆盖。

两组均重新生成 90 份回答，保持同一历史 11 文档/72 块、90 题、查询向量、Milvus Top8 和真实 BGE 顺序，只改变单文档配额 1/2；最终仍最多三块，1200/3000 字符及 16384 JSON 字节。这样可观察增加同文档证据的收益与挤出其他证据的代价，避免复用旧 cap1 答案带来的模型时间差。没有同时改提示、支付知识、召回或阈值；本轮只读历史 publication，不访问当前 Spring/MySQL/Milvus，不能解释为当前在线权威快照验收。

### 触发、执行与额度

最终计划指纹为 `fb3f2ac0ab7b4d02df9185a2f26144b37b1b8e9743801e1498fe1f19653a4544`。可信用户答复的来源、范围和计划绑定保存在本机 `real-approval-receipt.json`；原 `authorization-request.json` 继续保持 pending，独立 `approved.json` 才供运行器使用。`rag_cap_experiment` 先验证批准、完整计划、输入与关键实现，再经正式 `answer_once` / `CustomerServiceRagService` 回放选择、预算和回答，底层发送前再次核对实际问题与上下文。单并发、题内交替、SDK 零重试，失败占额度，三连供应商故障停止；没有修补坏 JSON 后重算成功，也没有补跑。

```powershell
# 在 ai-service 执行；以下是本轮已执行的历史命令，正式账本已耗尽，禁止重跑或换账本。
uv run python -m ai_service.orchestration.rag_cap_experiment --live --plan ../tmp/rag-cap-answer-20261118/prepared/plan.json --approval ../tmp/rag-cap-answer-20261118/approved.json
uv run python -m ai_service.orchestration.rag_blind_review --report ../tmp/rag-cap-answer-20261118/prepared/live/report.json --export ../tmp/rag-cap-answer-20261118/real-blind
```

真实运行从 UTC 11:23:12.821 至 11:26:18.432，约 **185.61 秒**，退出 0。新账本为 1 行授权与 **180 行唯一回答预占**，题序与计划、结果一致，全部有供应商响应及 usage；新 Embedding、BGE 推理、INDEX、生产写入均为 **0**。180 份实际上下文与最终计划逐字一致，两组检索/重排输入相同，65 题选块变化、25 题相同，没有字符或字节裁剪。

请求配置为 `deepseek-chat`、温度 0.1、输出上限 8192，但 **180 份响应元数据均自报 `deepseek-flash`**，且 system fingerprint 相同。源码核对确认零重试客户端使用冻结的 `settings.model_name`，报告直接保留 SDK 返回内容；无法从这些记录认证实际供应商模型身份或路由稳定性。因此本轮只能描述为“请求 deepseek-chat 的同一兼容接口对照”，不能声称已验证原生同名模型。未为探测模型身份追加付费请求。

### 故障、盲审与一致性复核

cap1 为 **89 ok / 1 error**，cap2 为 **88 ok / 2 error**，两组均无 not_run。三项 `MODEL_PARSE_FAILED` 为 `preview-history-004` 双组及 `deployment-download-003` 的 cap2；原始响应均在 `citedChunkIds` 前缺少起始双引号，结束原因均为 stop，没有输出截断信号。三次错误不连续，未触发三连停止。故障保持每组 90 分母，不能算正确拒答；只读重放正式解析器复现 **177 接受 / 3 拒绝、0 网络尝试**，不改变原始响应。

`rag_blind_review` 导出 180 张匿名卡，私有映射独立保管；三个全新代理各审随机 60 张，均未获得组别、原报告、旧答案或其他分片。三份 complete 评审逐事实、逐断言、逐引用核对实际 contexts，严格导入后合并；角色始终为 agent，原人工模板仍全部 pending。软件只验证结构及逐字摘录的来源，不证明语义判断和断言穷尽。

初审为 cap1 **75/90**、cap2 **79/90**。随后独立匿名复核覆盖全部初审失败及同题另一份、所有代理提出的疑义，共 **18 题/36 卡**，不是再次审阅全部 180 张。修正侧车包含 **13** 份完整 review：三项必要事实 missing→met，其余为断言拆分、引用映射、撤去匿名故障的过早归因及下载题疑义。三项事实是 cap1 的多文件不等同 Vue、支付集成需另核验、首次使用中的生成后查看结果再迭代；统一采用冻结等价表达，不把关键词或可选后续方向升级为必答清单。原三片、初版评审/汇总、复核包及最终应用结果均分别保留。

`deployment-download-009` 两组均命中冻结的“下载无需先部署”事实，但实际 contexts 对负前置只有隐含支持；两组统一标为 annotationDispute，该 claim 保守记 unsupported，等待人工判断。另一个“部署使用构建产物”未限定 Vue 的范围错误独立保留，两份本来已失败，新增疑义不改变总体通过数。没有用源码事实成立替代模型实际输入的证据支持。

### 本轮代理结果与失败归因

主通过要求全部必要事实满足、没有错误/无依据断言、引用支持、相关性完整且可答性正确。原冻结标准下的复核后结果为：

| 指标 | cap1 | cap2 |
|---|---:|---:|
| 全部答案通过 | **77/90（85.56%）** | **79/90（87.78%）** |
| 可答题通过 | 68/81 | 70/81 |
| 拒答题通过 | 9/9 | 9/9 |
| 公共必要事实满足 | 109/130 | 113/130 |
| 正常响应中的误拒答 | 3 | 2 |
| 请求故障 | 1 | 2 |
| 无依据断言（含待人工的隐含支持） | 2/341 | 2/338 |
| 有支持的引用 | 116/116 | 121/122 |

题级配对四格为 **都通过 75、仅 cap1 通过 2、仅 cap2 通过 4、都失败 9**。cap2 净增 **2/90＝2.22 个百分点**，固定 seed=20261118、10000 次以题为单位 bootstrap 的 95% 差值区间为 **[-3.33, +7.78] 个百分点**，包含零。90 题仍是反复使用的开发回归集，`humanGold=false`；这些是代理初审及复核结果，不能据此声称稳定收益、独立泛化或人工验收通过。

四个仅 cap2 通过案例为 `platform-account-001`、`admin-knowledge-005`、`community-007`、`community-013`。本轮新增的同文档第二块确实进入最终上下文、被引用并提供首次使用流程、禁用异步边界、作者/管理员例外和完整求助模板。确定性选择变化可追溯，但每组只生成一次，不能将证据链扩大为稳定模型因果效应。

仅 cap1 通过的两个案例为 `platform-account-005` 的 cap2 多引了一块只列问法的 FAQ，以及 `deployment-download-003` 的 cap2 坏 JSON。前者观察到配额放宽使问法块入选和模型误引；后者的实际上下文已支持答案，不能将协议错误直接归因于 cap2。两组的下载题仍将 Vue 的局部构建规则泛化；这类生成范围错误不是单纯多给一块就能消除。

复核后仍缺失的 **38 条公共必要事实**逐条核对实际输入、Top8、规范块及 selected/dropReason：同文档配额丢失 **18**、未进入 Top8 **12**、证据在场但生成遗漏 **3**、TopK 截断 **1**、请求解析故障影响 **4** 条事实（对应 3 次请求）。这些是事实计数，不能与请求计数相加；没有观察到字符/字节预算裁剪。复合事实逐子项核对，不使用整段标注 quote 未存活来证明事实丢失。例：发帖的登录/标题正文/有效标签已经在输入，模型却只给推荐写法，归生成遗漏；知识库文件类型与大小仍在同文档第三/后续块，cap2 也未选入；活动单页类型、复杂组件以及完整部署步骤的关键块未进入 Top8，放宽配额无法恢复。

支付新口径单列：用户早已确认当前平台无支付功能，应直接说“目前平台不支持支付功能”。本轮两份答案仍仅说明按钮不代表接入、需要另行验证，**两组都不满足新口径**。原语料没有直接给出当前不支持事实，本轮没有将这项新知识混入 cap 单变量，也没有覆盖冻结主分数；新语料/标注版本及 INDEX、真实验证仍待另行安排。

### 成本、验证与交付边界

| 本轮观测 | cap1 | cap2 |
|---|---:|---:|
| input tokens | 64464 | 64904 |
| output tokens | 6845 | 6882 |
| total tokens | 71309 | 71786 |
| input 中 cache_read tokens | 46078 | 39168 |
| 回答平均耗时 ms | 980.55 | 971.35 |
| 回答 p95 ms | 1445.17 | 1547.80 |

usage 包含三次失败，cap2 total tokens 多 477（约 0.67%）。cache_read 是 input 子集，不重复相加。未冻结实际计费单价，金额保持 null；共享历史检索/BGE 的耗时不计作本轮新工作，这些是回答调用观测，不是在线 E2E 或性能稳定性结论。

本轮重新执行工具相关六文件 pytest，**199 通过、0 失败/错误/跳过、2 条依赖弃用警告**，退出 0；180 卡严格导入、36 卡复核应用、额度/输入核对和三次坏 JSON 解析回放分别保留。独立方法审计 **24/24** 检查通过、0 阻断问题，重算初版/最终四格并核对 32 份证据摘要、三项事实更动和保护文件；审计不替代语义或人工裁决。命令与证据：

```powershell
# 在 ai-service 执行；无新增真实调用。
uv run pytest tests/test_rag_cap_contracts.py tests/test_rag_cap_budget.py tests/test_rag_cap_experiment.py tests/test_rag_rerank_experiment.py tests/test_rag_blind_review.py tests/test_rag_cap_blind_review.py -q --junitxml=../tmp/rag-cap-answer-20261118/tool-validation/real-round-tool-tests.xml
uv run python ../tmp/rag-cap-answer-20261118/replay-real-parser.py
```

临时保护审计首试将 README diff 连同 Git index 摘要及 CRLF/LF 进行字节全等比较而退出 1；确认实际用户 hunk 未变后，仅对齐原保护脚本的语义，忽略 index 元信息并统一 diff 输出换行，保留完整 hunk 比较。原失败记录保存在 `tool-validation/real-audit-attempt-1.txt`，不涉及真实运行失败或文件恢复。第十五轮的 1889/25 是历史全套设施成绩，本轮未重跑全套、Java、前端、compileall 或 lock 检查；本轮跟踪文件仅新增结果及文档，生产 Python 源码/依赖未改。

原 `.env`、耗尽的旧 184 行账本、旧报告、两轮回放、取消计划和前端三项用户修改共九份保护文件保持字节不变；README 原围栏 hunk 未暂存。新增真实账本也保持完整耗尽状态，不能续跑、换账本或目录再用额度。没有启动/重启正式 Spring/Python 服务、改变生产 cap1、发布支付知识或进行当前双服务/浏览器 E2E。

原始答案、实际 contexts、usage、私有映射、三份初审、修正侧车及归因均留在 `tmp/rag-cap-answer-20261118/`；脱敏聚合为 [cap-answer-20261118-v1.json](knowledge/product/evaluation/results/cap-answer-20261118-v1.json)。本轮只提交必要结果与交接文档，执行代码基线为 `346c26e`，本轮交付提交哈希随最终回复报告。人工验收按用户安排继续待办。

### 下一轮建议

保留生产默认 cap1，cap2 继续作为候选：当前四个恢复案例有明确证据，但净收益小且区间包含零，尚不能替代人工产品裁决。下一项建议先单独验证客服回答的供应商 JSON 输出约束：本轮三次格式故障都在非截断响应中缺少同一字段起始引号，而现有客户端仅靠提示及严格解析，没有供应商 `response_format`。先用已落盘原文和 Fake SDK 证明请求参数、严格字段/引用校验及拒绝不支持供应商的边界，不以字符串补引号或自动重试冒充真实成功；模型/代理仍可能忽略或不支持约束，模型身份差异也需明确。新真实对照须另列计划与额度，当前 180 次已耗尽。

证据选择仍需后续独立研究：cap2 无法覆盖同文档第三块、Top8 未召回或生成遗漏，不能继续通过扩大配额同时混入其他变量。支付候选应另建知识/标注版本，人工验收模板及下载负前置疑义保持待办，不在本轮直接推广。

## 第十七轮：客服 JSON 输出约束方案准备（2026-11-18，待确认）

### 问题、证据与方案取舍

用户要求继续下一轮优化，本阶段承接第十六轮的 JSON 格式故障方向。历史 180 次真实回答中三份在 `citedChunkIds` 前缺起始引号，均 finish_reason=stop；177 接受/3 拒绝属于上轮原始响应与解析回放，不能记为本轮新测试。只读源码核对确认正式客服当前仅通过提示要求 JSON，尚无供应商 `response_format`。

候选方案为默认 text、客服单次请求可显式配置 json_object；仍用现有严格字段、引用及拒答校验。相较强化提示，该方案增加传输层格式约束；相较 strict json_schema，适配面更宽、改动较小，但不保证代理支持、语义正确或字段符合业务要求。完整设计与三个方案取舍见 [JSON 输出约束候选设计](../docs/superpowers/specs/2026-11-18-customer-service-json-output-design.md)。具体请求模式、兼容边界尚待用户确认，当前没有实施或启用。

### 拟定触发、流程、模块与边界

拟新增 `AI_SERVICE_RAG_ANSWER_RESPONSE_FORMAT=text|json_object`，默认 text 不发送任何新字段；只有 `OpenAICompatibleModel.answer_customer_service` 显式 opt-in 时将固定格式参数传入该次调用。不修改共享客户端，不影响生成、路由、审查、修复、预算或 tracing；不做字符串修补、格式重试或静默 text 降级。

供应商/SDK 报错、忽略约束返回坏 JSON、length/content_filter 提前报错和取消必须分别验收。客户端原有 SDK 默认重试并未在此阶段改变，不得称正式服务零重试。旧 rerank、cap 独立 checker、snapshot 与 refusal 实验拟在资源、输出及预算动作前拒绝非 text，以免改变冻结条件或因旧 `_invoke` 参数不兼容消耗额度。

### 本阶段验证与交付状态

本轮目前仅完成只读调查、设计自审、文档事实与保护文件核对；没有生产代码、依赖或真实配置改动，没有运行 Python/Java/前端测试、启动服务或调用真实模型。新增 Chat、Embedding、BGE、INDEX 和生产写入均为 0；上轮耗尽额度不复用。文档提交哈希随本阶段回复报告。

16 份保护文件以 `tmp/rag-json-output-20261118/initial-state.json` 为基线，涵盖真实 `.env`、两份历史账本、原报告/回放/评审、人工模板、支付澄清与两仓既有修改。既有 README 围栏修改和取消回归计划不纳入本轮提交，前端三项修改保留。人工验收继续待办，生产 cap1 与支付语料保持原状态。

### 确认后的下一步

确认完整设计后编写实施计划，再验证真实 SDK→本地 MockTransport 参数序列化、同适配器并发隔离、严格解析、失败关闭、预算与取消，以及旧实验前置拒绝。历史 180 份响应只读回放到新目录，用于验证解析兼容，不冒充新约束下生成的答案；新增代码按范围补齐文档并执行 Python compileall、全量 pytest、锁检查及限定中文提交。供应商实际遵守程度、故障率和答案质量仍需新计划及单独额度的同条件真实对照，离线通过不能代替这些结果。

## 以后每轮必须使用的交接格式

1. 本轮解决什么：原问题的具体触发场景、证据和目标。
2. 为什么选此方案：收益、代价、未选方案及不适用条件。
3. 怎么做：触发条件、判断依据、处理流程、关键模块与协议/配置变化。
4. 边界与故障：并发、重试、取消、旧版本、丢响应、资源不可用如何处理；哪些保证仍不存在。
5. 验证证据：离线/真实分开，命令、计数、失败及修复、成本/预算与未测项；禁止沿用历史结果冒充本轮。
6. 交付状态：提交哈希、已部署范围、当前资源与回退限制，保留无关用户修改。
7. 下一轮：问题/原因/实现路径/边界/预定验收；每轮只推进可归因变量，不自动升级质量候选。
