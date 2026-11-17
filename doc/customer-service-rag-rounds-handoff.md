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

## 以后每轮必须使用的交接格式

1. 本轮解决什么：原问题的具体触发场景、证据和目标。
2. 为什么选此方案：收益、代价、未选方案及不适用条件。
3. 怎么做：触发条件、判断依据、处理流程、关键模块与协议/配置变化。
4. 边界与故障：并发、重试、取消、旧版本、丢响应、资源不可用如何处理；哪些保证仍不存在。
5. 验证证据：离线/真实分开，命令、计数、失败及修复、成本/预算与未测项；禁止沿用历史结果冒充本轮。
6. 交付状态：提交哈希、已部署范围、当前资源与回退限制，保留无关用户修改。
7. 下一轮：问题/原因/实现路径/边界/预定验收；每轮只推进可归因变量，不自动升级质量候选。
