# B0b：客服知识权威发布协议

依据：用户要求继续逐轮交接中的下一步。沿已批准路线B推进；候选组织及在途读语义是本次待确认的具体取舍，不将已有租约等同于跨存储原子性。

## 问题与选择

旧执行可以在最后一次许可检查后暂停，随后恢复写Milvus或覆盖alias。MySQL的owner CAS只保护数据库，不保护alias或共享活动块。新协议以Spring/MySQL发布事实为权威，Python只生产私有候选；所有新读取使用权威具体collection。

实测当前72 chunks+11 manifests，共83行、254976个向量元素，UTF-8 JSON 4586359字节。推荐有界整库不可变候选：单集合查询、可复制已验收向量、不需要文档候选查询扇出；代价是每次变更复制快照。按文档候选写放大低，但带来查询扇出和全局TopK合并复杂度，留到超过现有预算时另做设计。不得为了复制快照提高已有文档/块/向量预算；另设候选序列化字节上限64MiB，超限明确失败。

## 不变量

1. publicationId稳定使用`task_<outboxId>`，attempt仍为独立operationId/fence。每次Python准备创建新的随机候选名，绝不重写已存在候选。
2. 候选不改当前集合、alias或旧manifest，不删除旧publication集合。新kind与命名空间独立，旧回收器不得将其视为可清理staging。
3. 同一Spring事务内核对当前epoch/活动源、claim owner与deadline、有效租约、当前业务版本/禁用状态；登记不可变publication、切活动指针、INDEX serving三元组、任务SUCCEEDED和alias outbox。
4. 相同publication重复提交相同收据返回原事实；收据不同则冲突。已提交事实可在原lease失效后查询；不能换ID绕过未知状态。
5. alias只作镜像。读取不通过alias；数据库/内部API不可用不回退旧alias。alias迟到覆盖不改变权威读取，周期性对账在写入静止后收敛。
6. 管理员文档变更在同一事务先锁知识库epoch，再改文档/入任务并递增epoch；所有新提交走相同锁序。禁用/删除立即从权威读取白名单移除，不等待物理候选发布。

## 内部协议（整数须在HTTP中保留JSON number）

所有新API要求现有内部Bearer认证，不记录proof、签名URL、原文或向量到日志。普通sources协议不变。

`DocumentIdentity`字段：`documentId:string, documentVersion:int, indexRevision:int, activationRevision:int, sha256:string, fileName:string, fileType:string`，列表按documentId排序、无重复；fileType规范为大写。

`PublicationContext`：`publicationId:string, baseEpoch:int, sourceCollection:string|null, documents:DocumentIdentity[]`。documents是操作完成后的预期精确集合。

`CandidateRequest`：`context:PublicationContext, mutation:现有INDEX/DELETE/REBUILD请求`。复用现有严格请求schema，包括lease、索引身份及下载校验；只允许新版正revision。

`CandidateReceipt`：`publicationId, baseEpoch, operationId, fence, candidateCollection, snapshotHash, embeddingModelVersion, embeddingDimension, documents`。其中documents包含DocumentIdentity全部字段及`chunkCount:int`。不含下载地址或proof。snapshotHash绑定按id排序的完整块/manifest（f32向量、正文和定位），不包含仅用于该attempt的fence/活动位。

`ReadSnapshot`：`enabled:bool, epoch:int, publicationId:string|null, collectionName:string|null, embeddingModelVersion:string|null, embeddingDimension:int, documents:DocumentIdentity[]`。documents是当前serving且未禁用/删除、激活仍匹配的白名单；可能小于物理快照。

Spring内部入口：GET `/internal/customer-service/knowledge-publications/snapshot`；GET `/internal/customer-service/knowledge-publications/{publicationId}` 查询事实。worker在本地事务Service执行prepare/commit，不绕HTTP访问自己的数据库。

Python内部入口：POST `/internal/v1/customer-service/knowledge:prepare-publication`，返回CandidateReceipt；POST `/internal/v1/customer-service/knowledge:reconcile-alias`，请求`collectionAlias,collectionName,publicationId,epoch`，执行前后与Spring权威快照核对，绝不回滚到旧指针。

## 写路径

Spring新模式worker准备原操作及lease后，prepare在短事务核对owner/租约/epoch，获取原活动具体集合，计算预期文档名单。INDEX名单用本次内容/索引/激活替换目标serving；DELETE排除目标和已禁用项；REBUILD使用当前serving历史原文。构建期间不持数据库事务或行锁。

Python在同一请求的许可边界内复制源快照中预期身份的完整manifest/块；严格比较名单、数量、正文hash、原文hash、定位、模型/维度。INDEX只为新索引调用原ETL下载/解析/Embedding；REBUILD复用原解析校验及原向量；DELETE只删除候选中的目标，不改源集合或写共享活动位。允许DELETE最后一篇产生空权威集合；REBUILD保留已有非空库的空候选阻断。

候选使用`<alias前缀>_pub_<UUID32>`命名，元数据kind为`customer-service-knowledge-publication`，记录aliasHash、模型、维度、schema、publicationId/baseEpoch/operationId/fence。写入全部行后逐值回读，最后写完成标记、绑定snapshotHash；只有完整回读验证后才返回收据。失败保留不可发布候选，不自动删除可能已被其他提交引用的集合；回收另做设计。

Spring commit再次锁epoch→任务→租约行，核对owner、未过期claim、当前签名租约及预期文档集合；禁止仅凭返回status成功。INDEX同事务更新serving；所有操作同时落任务成功及publication事实/活动指针/alias outbox。并发其他文档提交也推进epoch，旧候选提交失败，可按既有任务重试从新源准备，不能把旧候选强行发布。

## 读路径和在途语义

新配置开启时使用权威store适配器：先取ReadSnapshot，再向具体collection按允许documentId过滤检索，逐条核对内容/索引/激活及原文hash。模型签名、维度和候选元数据/完成标记不匹配时失败关闭。保持原Top8/每文档1块/final3，禁止借协议改动推广质量候选。

每请求独立保存读epoch，模型调用前及模型返回后复核epoch。变化或数据库故障则不投递旧结果，不自动重试付费模型；返回既有不可可靠回答/降级协议。最后复核后仍可能发生撤销，不能承诺绝对即时撤销；稳定epoch期间的问答采用该快照语义。空白名单不调用回答模型。

## alias 对账

Spring定时worker读取当前权威head并调用Python对账；旧outbox标SUPERSEDED，只有确认当前head镜像成功才ACK。即使所有事件已ACK，也周期性核对当前head，修复迟到旧alias写。迟到写不是权威回退，查询仍使用具体collection。对账失败不撤销publication，不删除旧集合；观测仅记录稳定ID/错误码/计数。

## 开关、迁移与回滚

两端新增显式publication开关，默认关闭以保持现有单测/旧库兼容；新模式开启但schema或权威head不存在时拒绝新任务/读取，不降级旧协议。启动只读校验持久mode；已经启用的库不得通过关闭开关回到alias权威，需明确报协议要求。

迁移SQL只新建epoch/active state、publication、alias outbox，不伪造旧发布事实。先停写/排空/备份、统一部署两端，再通过明确维护开关和管理员bootstrap入口，从指定已核对的旧具体collection构建新的私有完整快照，验证现有serving，条件提交首个publication并写持久启用标记。bootstrap不改变内容/索引/激活身份、不调用Embedding。启用后关闭维护入口。禁止混跑旧实例、切旧alias或仅回退旧二进制；回滚需排空并按权威事实另行迁移。

管理员bootstrap入口：POST `/admin/customer-service/knowledge/publications/bootstrap`，body `sourceCollection`；要求ADMIN、显式`publicationBootstrapAllowed`、无活动head/未完成任务、精确source命名校验；使用REBUILD许可、当前registry原文及完整候选验证，Spring本地事务提交。已存在head时拒绝再次bootstrap。

## 验收与限制

离线与真实MySQL/Milvus隔离矩阵：私有候选不影响源、旧owner检查后暂停、接管、禁用/启用/重建交错、epoch冲突、同publication重复/冲突、提交前后故障、提交确认丢失后的事实查询、alias失败/迟到覆盖/周期收敛、缺权威目标/坏完成标记/模型错配、预算/空集合、在途回答epoch改变不泄露旧结果。两个Coordinator或Fake时钟不冒称完整双实例网络故障注入。

首个生产bootstrap只有隔离协议测试、全部必要Java/Python回归及备份完成后执行；本轮不新增真实回答调用。旧原文registry、旧集合和墓碑保留，不新增无引用证明的自动GC。模型升级、分块策略变化、无限规模、多轮长期记忆与人工质量推广均不在本次范围。
