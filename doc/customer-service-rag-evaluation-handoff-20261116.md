# 客服RAG评估交接（2026-11-16）

## 本轮范围

阅读并适配[Datawhale系统评估章节](https://datawhalechina.github.io/all-in-rag/#/chapter6/18_system_evaluation)，在11月13日方案基础上落地独立评估工具；不修改生产客服检索、模型提示、知识版本任务、数据库或Milvus alias。

具体方案和取舍见[评估与优化设计](../docs/superpowers/specs/2026-11-16-rag-evaluation-optimization-design.md)。核心建议为先修版本契约，再按阶段证据做有界证据组合与定向召回；两块候选暂不推广。

## 新增内容与使用

- `ai-service/evaluation/customer-service-boundaries-v2.json`：48题、24组、40可答/8拒答、63条精确原文事实；前32题重标注，新增16题，待人工复核。
- `rag_evaluation_workflow.py`：来源校验、块级IR、事实span存活、分组/配对、标准单轮字段导出。
- `rag_snapshot_experiment.py`：默认离线，显式真实Embedding/GPU重排/回答对照；调用预占上限、无自动重试、失败/缺失单列。
- `rag_evaluation_judge.py`：可选匿名语义评审，核对claims/证据原文/事实ID；仍存在模型偏差与语义误判。
- 两套新增测试：评估分母、来源/预算/裁判边界，以及已知版本组合冲突的可重复诊断。

在`ai-service`执行：

```powershell
uv run python -m ai_service.orchestration.rag_snapshot_experiment
uv run python -m ai_service.orchestration.rag_snapshot_experiment --live --judge --judge-case-limit 12 --max-chat-calls 120 --output ../tmp/rag-study-new-run
uv run python -m ai_service.orchestration.rag_snapshot_experiment --analyze ../tmp/rag-study-new-run/report.json
```

第二条有真实费用且要求输出目录尚不存在。完整48题需要96次回答，12题两组评审24次，合计120次聊天预占，另计Embedding。不能覆盖账本、自动重试或换目录绕过本次授权总量。模型和Embedding密钥读取私有配置，不输出到日志。纯校验不初始化供应商。

`--analyze`重新计算分组和逐题差异，并导出`user_input/retrieved_contexts/response/reference/metadata`。其中contexts是实际最终模型输入，错误保留null；不代表已经执行Ragas框架。拒答题需要适用性判断，不能因空答案得到虚假的忠实度满分。

## 真实实验记录

本轮数据指纹：`f3e59e9a68ee15938397c5a822c42fdcd94c79962fd9c8fe0f270b3fc31285e2`；公共语料指纹：`d52ab6a099688fc3a718c52d41e1597dc5df34361b4bd8a10bbc200e42315b8f`；归一化向量快照指纹：`2ce9389865ed887a95c250746d76292b3941b1c62c6733eeda2b2a1d7d36f077`。

完整私有产物在`tmp/rag-evaluation-20261116/live-snapshot/`：`report.json`、`retrieval.jsonl`、`calls.jsonl`、`vectors.npz`、`analysis.json`、`samples.jsonl`。不提交原始产物；本文仅保留脱敏聚合与问题ID。本轮版本未在report记录开始时间与实现哈希字段，后续运行已补充；不能追称旧产物含有这些字段。

| 项目 | 基线每文档1块 | 候选每文档最多2块 |
|---|---:|---:|
| 回答可答题 | 40/40 | 40/40 |
| 拒答契约 | 7/8 | 7/8 |
| 最终原文证据宏覆盖 | 90.208% | 93.125% |
| 完整证据题 | 33/40 | 35/40 |
| 回答错误/缺失 | 0/0 | 0/0 |
| 语义评审有效 | 12 | 11，另1次无效 |
| 回答均值/P95 | 1025/1396ms | 1002/1370ms |

真实调用：Embedding4批、120输入、8916 token；聊天120次，其中回答96、评审24。聊天usage为86991输入+9975输出=96966 token，不含Embedding。语义有效样本的支持claim为24/24与17/17，但等距抽样未覆盖关键失败，且两组分母不同，不能称全量满分或人工正确率。

共享Top8必要证据宏覆盖99.375%（39/40完整），重排Top3降到93.125%；现状文档去重进一步降至90.208%；本轮字符/字节裁剪没有新增事实损失。3题改善002/006/007，1题标注覆盖降低032，回答数量无变化。

## 已确认失败与下一步

- 026：越权部署执行请求被输出为answered=true的权限解释。并未执行部署或泄密，但不满足当前内部false/空/无引用契约，两组均不能通过安全门槛。
- 021：所需证据在dense第6位，仍没进入最终证据；应查重排与覆盖选择。
- 029：三种类型所需证据均已召回，但重排及单文档配额造成缺失；最多2块也不能普遍解决同文档3事实。
- 030：Vue证据dense第11位，Top8确有召回不足；有依据比较Top16/24、标题输入。
- 032：丢失9张上限标注但仍正确回答不自动同步，先核对标注必要性，不自动判回答退步；004也需补等价来源，007需核对额外审核提示是否必答。

标注冻结，不根据本轮结果改gold抬分。人工裁决后另建v2.1，两组同等重算。下一轮裁判必须加入这些Bad Case和安全对照，不能只扩大简单题抽样。

版本诊断在Fake中复现相同版本换ETL冲突、禁用后启用受墓碑阻断、重建ETL与后续索引冲突、空重建切空集合。测试通过表示缺陷可复现，不表示已修好；目标生命周期修复仍按11月13日方案单独实施。

## 验证与边界

本轮执行Python compileall、pytest与`uv lock --check`；全量最终结果见本节末尾的交付校验记录。模型评估为真实Embedding、真实GPU BGE与真实回答/裁判；数据库、Milvus、OSS及生产HTTP未参与该隔离实验。初查Docker daemon和8000服务不可用，因此不得沿用11月12日环境结果称为本轮通过。

原90题保持不变；没有生产推广、真实管理员生命周期、多实例过期租约、在途禁用、ANN过滤或完整复杂PDF验收。正确性与质量方案需用户审阅取舍后再改生产行为。

交付校验：最终`compileall -q src`与`uv lock --check`通过；全量pytest **1452 passed、14 skipped、2 warnings**（241.43秒），14项真实PostgreSQL集成测试未opt-in，警告为Starlette及LangGraph弃用提示。新增专项共26项包含在全量内，不重复累加。另对账本124条预占、96组结果、23份有效裁判、96条标准导出做离线一致性校验，无新增模型调用；`git diff --check`通过。

## 用户批准路线 B 后的实施续轮（2026-11-16）

上文为批准前一轮的历史证据，不能将其验证次数或缺陷结论冒充本次执行结果。用户随后明确要求按推荐方案实施。本次实际范围为 B0a 版本基础、B1/B2 单变量对照、B3 拒答候选及 B4 重点校准队列；完整路线 B 尚未全部完成，进度见 `docs/superpowers/plans/2026-11-16-rag-route-b-implementation.md`。

### 版本改动、触发条件及处理

原问题：同内容 reindex 改 ETL 却撞不可变 manifest；禁用后启用被内容版本墓碑永久拦截；全库 build ETL 污染文档索引身份；替换失败后的旧 serving 原文没有登记。现在 Spring 保持内容版本，按任务 ETL 分配稳定 indexRevision，启用分配新 activationRevision；重试不换索引身份。成功落账同一条件 UPDATE 保存 serving 内容/索引/激活三元组。原文 registry 每个内容版本独立记录 OSS 键，重建使用 serving 原文，缺关联明确失败。

Python 物理块/manifest 绑定这三个身份，新增索引不允许同内容版本偷换原文 hash；同索引重试仍严格比较块集合。激活墓碑按文档撤销不高于目标激活的全部代际，新激活不清墓碑也可启用；迟到旧删除不会误伤新激活。持久协议标记独立于 alias，不能用旧 REBUILD 换集合后恢复旧 INDEX/DELETE。重建同一索引必须核对旧物理集合清单及完整正文、locator、向量；变了要求新 INDEX，缺 v2 清单明确不可验证。空候选不得覆盖已有 alias。读路径在向量搜索前比较 query 模型签名和集合元数据，避免同维不同空间被静默接受。

17项新增生命周期/HTTP边界测试覆盖新串联、迟到撤销、失败替换、不可变身份、旧协议降级、同索引改变正文/定位/向量、缺原清单、同维模型错配、非法/部分 revision，以及实际 HTTP→ETL解析→存储函数链。下载、Embedding 和 Milvus RPC 仍是替身，不能称真实管理员跨系统验收。旧协议诊断保留作兼容失败回归，不再把其通过当作新版目标修复的证明。

迁移必须按[独立操作说明](customer-service-knowledge-v2-migration.md)先停写排空、备份与 SQL 检查、同步部署，再逐文档新 INDEX 验收，最后全库重建。无法从旧当前对象推断历史 serving 原文时脚本阻断，不伪造登记。模型签名变更不能直接向旧模型集合写入，需另行候选迁移。历史记录/墓碑查询仍有原有有界上限，未新增无限历史或自动回收保证。

### B1/B2 实际对照

使用冻结48题及原72块/单位向量；没有新增聊天/Embedding。cap1/2/3 沿用旧 Top8 BGE，扩大 TopK 则本轮同一真实 CUDA BGE 实例对8/16/24各48题重评，共144次；另一次探索96次本地评分，合计240次，均不混入付费聊天次数。

| 独立变量 | 最终精确证据宏覆盖 | 完整可答题 |
|---|---:|---:|
| 同文档 cap1 | 90.2083% | 33/40 |
| 同文档 cap2 | 93.1250% | 35/40 |
| 同文档 cap3 | 93.1250% | 35/40 |
| dense Top8、cap1 | 90.2083% | 33/40 |
| dense Top16/24、cap1 | 89.5833% | 33/40 |

Top16/24 dense 召回覆盖虽为100%，但030被同文档无必要事实块挤出，021/029仍有组合缺口。此指标不等于语义正确率，不据此推广 cap3 或扩大 TopK。标题只导出 `title-path-body/v1` 输入及 hash，未生成新向量；父章节扩展未实现。完整变量、逐题定位与SHA见[有界证据实测](rag-bounded-evidence-experiment-20261116.md)。

### B3/B4 实际对照与推广决定

新增16题八组成对诊断，保留v2全部8条拒答原题，配8条公开说明反例。两臂使用相同历史实际最终上下文，R1/R2每题各三轮，串行交错，共96次真实聊天；调用前 fsync 预占、失败占额、无重试/补调，账本97行包括1行上限和96行预占。没有调用 Embedding、检索、语义裁判或写生产索引。

| 项目 | R1现状 | R2候选 |
|---|---:|---:|
| 拒答契约 | 21/24 | 24/24 |
| 公开题可答/误拒 | 24/24、0 | 24/24、0 |
| 错误/缺失 | 0/0 | 0/0 |
| 平均/P95 | 929.1/1323.7ms | 928.6/1236.2ms |
| 聊天 token | 36,772 | 50,860 |

026原越权部署请求三轮R1均给公开权限解释，R2均满足false/空/无引用。R2明确先判断当前要求执行的任务，不能把越权命令自动改答成“是否允许”；引号中的命令、否定执行和只问公开规则仍允许基于证据说明，没有用关键词硬拒。但代理逐题审阅发现公开016第1/2轮把“具体用户当前任务状态”扩大为“账号状态应到应用页查看”，证据支持范围存疑。因此 **R2暂不推广，生产R1不变**。可答数不等于事实正确，人工门槛未通过。

总token87,632，R2较R1增加约38.3%，无冻结币种单价，不报虚构费用。配置模型别名deepseek-chat，供应商返回deepseek-flash；每次记录实际模型/指纹/usage，不能据别名保证权重不变。96条代理审阅有答案SHA及证据quote，不冒充人工裁决。重点人工队列112项（本轮96+历史002/006/007/021/026/029/030/032两臂16项），仍全部human-review-pending；007/032必要性争议与004等价来源不观察成绩后就地改gold，未来人工批准后发v2.1两臂同等重算。直接注入执行变体、独立盲测及实际语义裁判误差量化未覆盖。

本机原始证据在 `tmp/rag-evaluation-20261116/refusal-r2-live/`：report/calls/agent-review/human-calibration；report SHA-256 `51639d09a600678778776b3c3d29cedd6115198aaee07a7d9ca4f659bdb8a9b5`，与运行器源码哈希对齐。原始运行产物不提交。

### 本次验证及未完成事项

最终 Python `uv run pytest --tb=short`：**1518 passed、14 skipped、2 warnings，136.87秒**；compileall与uv lock检查通过。新增17生命周期+25有界实验+24拒答工具测试共66项包含在全量中。14项真实PostgreSQL集成未启用，警告为Starlette/LangGraph弃用。首次全量发现旧响应多出零值字段及本地客服开关污染测试夹具，已保留旧响应形状并显式隔离离线夹具，再全量通过；后续签名保护要求的测试模型配置也已对齐。

Java五组知识单测 **67/67**：AiClient11、Worker24、Schema10、Service15、Finalizer7；主代理核对surefire零失败/错误，并执行 `mvn clean -DskipTests compile -q` 成功。git空白检查通过。本次未做前端变更。

Docker引擎初查不可用，已尝试后台启动Docker Desktop，随后docker info仍超时12秒；已请用户检查Docker/WSL启动界面。没有执行真实MySQL迁移、OSS历史对账、Milvus生命周期、管理员API全90题、双实例迟到owner或在途撤销。Spring权威发布指针、稳定publication账本、私有候选/epoch提交和alias outbox属于B0b，**尚未实现**；当前租约检查不证明跨资源原子性，不能声称整个B0或路线B已完成。

下一步先恢复依赖，在隔离数据中验迁移和串联，再实施B0b；质量侧先裁决公开016及标注争议，随后用有界互补证据选择解决021/029/030，标题输入单独实验。清理墓碑、直接改TopK/提示默认、或仅切旧alias都不能替代这些步骤。

## Docker 恢复后的真实资源续验（2026-11-16）

用户确认 Docker 可用、Redis 已启动，并授权启动 Python/Spring。Milvus 3.0.2、etcd、PostgreSQL 容器就绪；没有删除或覆盖用户的其他容器。Python checkpoint、知识库依赖、Spring 管理员知识健康均通过。这里更新上文的历史阻塞状态，不改写此前未执行的事实。

真实库迁移前共12篇文档（11 ACTIVE、1 DISABLED），outbox无PENDING/PROCESSING、16 FAILED/14 SUCCEEDED。停止本轮启动的 Spring worker 后，经已有 Spring datasource 的 Java 工具备份知识库四表，再执行一次 v2 SQL；12条原文 registry 及全部 serving 身份逐项校验通过。备份位于本机忽略目录 `tmp/knowledge-v2-backup-52f5d460-0c88-4176-a7a3-5a8a83be3184/knowledge.sql`，SHA-256 `84c0bce8b9f242cd784ebc700c98b8db0054663d3eedb67ae38ce7e8b9e54775`。DDL 不具事务性，备份恢复仍需单独人工审查，不提供自动回滚。

随后通过原管理员会话及新版 Spring reindex API 顺序提交11次INDEX，每次预先持久化预占，无自动补调，逐篇等待成功并核对 serving 三元组；11篇均成功，内容版本和激活版本仍为1，索引版本首篇5、其余3。真实 OSS 原文下载、Embedding、Spring许可校验和Milvus写入均实际参与。禁用旧文档未启用，旧失败任务保留审计。Milvus只读盘点确认11份v2 manifest、72个活动块，所有活动文档ID均与公开语料映射一致。

首次整库重建任务 `479828560495325184` 执行一次失败，稳定错误码 `KNOWLEDGE_VERSION_CONFLICT`；没有切换alias，失败候选已清理。离线核对11篇原文hash、解析/块内容、定位及manifest均可复现；追加一次预占、关闭自动重试的Embedding诊断，首篇7块与已存向量逐值一致。失败当次的候选向量未持久化，不能据后续成功重算认定当次向量相同，也不能断言provider非确定性是已证根因。

首次全量测试在在线Python独占重排器锁时有4项进程测试失败（1514 passed/19 skipped）；已停止自启动Python后重测，不能把资源冲突写成业务逻辑回归。真实隔离Milvus5项已显式opt-in通过（62.51秒），使用合成向量和进程内协调器，不代表多Spring实例仲裁验收。真实随机schema迁移测试4项通过，覆盖正常/禁用回填、未排空和未知serving阻断及备份中文/转义/NULL/时间/生成列往返；业务库只读验收另行执行。

真实环境记录保存在忽略目录 `tmp/rag-resource-acceptance-20261116/`，包含预占账本、健康结果、版本盘点和受控诊断。没有新增96次聊天对照或重跑90题；质量门槛和B0b未实现范围仍按上文执行。

用户随后明确确认采用原向量复用。候选修复位于 `KnowledgeEtlService.rebuild` 与 `MilvusKnowledgeStore._restore_rebuild_revision_vectors`：v2下载/校验/重新解析后传递无向量块，存储层在原 collection 变更许可内读取完整原快照，核对身份、模型、维度、manifest名单与数量、正文、locator及hash后复用原向量；原逐值复核、墓碑、fence及候选发布门禁继续执行。旧协议仍调用Embedding；缺原清单/块、模型错配、解析变化、坏向量明确失败，不回退重算。新INDEX仍计算新向量。该选择避免同版本重新计算与不可变身份的冲突，也减少重建外部调用；不适用于原向量已经丢失、需要模型升级或分块变化的场景。

新增13项专项覆盖供应商漂移不再调用、坏/缺快照、模型和维度错配、读后向量变化；连同原专项149项通过。最终全量 **1531 passed、20 skipped、2 warnings（146.79秒）**，20跳过项为14 PostgreSQL及6显式Milvus。compileall与uv lock检查通过。真实Milvus最终 **6 passed（72.53秒）**，含独立原向量快照复用、重建前后逐值相等；原严格重算冲突测试仍保留。Java最终 **71 passed、2 skipped**（67原知识测试+4隔离迁移），一次性实际迁移及业务预检入口在该批默认跳过；此前实际迁移和预检的执行证据独立保留。干净编译通过。

两条有预占、无自动重试的真实query Embedding+Milvus Top8烟测分别命中平台能力、下载部署目标文档（2/2）；没有调用聊天模型或语义裁判，不能据此宣称完整问答质量通过。

用户确认后已启动新版Python，并以独立上限1的预占账本提交REBUILD `479832495083827200`，一次成功。alias从 `customer_service_knowledge_9a3e5b421e0e_staging_8d353fc91f7f` 切至 `customer_service_knowledge_9a3e5b421e0e_staging_446409c019dc`；最终逐项比较72个活动块的全部校验字段及f32向量（仅忽略active/fence），全部相等。11份manifest与Spring内容/索引/激活serving三元组一致；原禁用文档保持禁用。最终只读MySQL审计无PENDING/PROCESSING，新任务SUCCEEDED，历史17个FAILED保留（含首次失败REBUILD）。这次v2 REBUILD未调用Embedding，OSS、Spring许可验证及Milvus发布真实参与。

Spring JVM17744持续监听8123，新版Python经 `uv run python -m ai_service.server` 后台启动；本轮运行时用缓存模型离线加载（HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE），不修改模型或检索默认值。一次直接启动出现客服依赖降级，改用缓存模式启动后全部依赖恢复；未据此声称已定位首次启动失败的精确根因。Spring本轮知识开关开启，timeout/claim/lease为180/300/240秒、batch2、retryMax1，均为进程环境覆盖，不改代码默认或私有.env。最终管理员健康coordinator/enabled/python全true，Python checkpoint及知识依赖全true。

后续优先实施B0b权威发布指针和对账，并补多实例迟到owner/在途撤销。当前快照复用仍依赖完整源集合；缺失或解析变化会失败，模型升级需独立候选迁移。质量侧R2及召回候选继续受人工校准/盲测门槛约束，不能把恢复索引当作质量提升已全面验收。

## 下一轮已执行：owner 与回执落账（2026-11-16）

逐轮统一入口改为 `customer-service-rag-rounds-handoff.md`，同时记录本轮和下一轮的问题、原因、方案、触发/判断/处理流程、边界、验证与提交。后续每轮必须追加并向用户用文字说明，不只给出链接。

本轮源码发现DELETE/REBUILD成功RPC后未检查finish条件写返回值：owner已接管时仍记录成功，数据库确认异常则误入普通失败/重试。已由`02b88e5`修复：只有命中1行记录成功，0行视为失去claim，Mapper异常保留落账结果未知且不立即重写失败或重试；自身lease仍按operationId/fence撤销。原实现新增场景红测4失败，修复后worker30通过；最终Java相关106项全部通过（99普通+7真实MySQL），干净编译通过。真实隔离schema覆盖不同物理连接竞争、接管、成功/失败Finalizer事务回滚、禁用/版本保护和双Coordinator互斥；最终7个schema均清理确认。

已重启Spring并复核健康；未调用收费模型、未重建或切换索引、未改业务schema。仍保持11篇/72块、原禁用状态、原alias目标及serving身份。此处“真实多连接/两个Coordinator实例”不是两套完整Spring/Python/Milvus发布系统；仅模拟持久丢回执状态，不声称网络故障或跨系统原子性已验。下一轮完整B0b设计、迁移/回退与验收要求见统一入口第三轮建议。
