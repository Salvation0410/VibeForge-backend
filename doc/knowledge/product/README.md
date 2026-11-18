# 产品知识与固定 90 条评测集

本目录将应用生成、社区等内容按逻辑分类组织，仍使用现有单一客服知识索引。分类不是多知识库检索功能；Markdown 和注册表中的状态字段不会自动触发 Milvus 过滤。

## 内容索引

| 分类 | 可导入正文 |
| --- | --- |
| 平台与账号 | [01 平台账号](documents/01-platform-account.md)、[11 功能边界 FAQ](documents/11-faq.md) |
| 应用生成 | [02 创建修改](documents/02-app-generation.md)、[03 需求规范](documents/03-generation-requirements.md)、[04 生成类型](documents/04-generation-types.md)、[05 素材保留](documents/05-images-preservation.md) |
| 预览与历史 | [06 预览停止与记录](documents/06-preview-history.md) |
| 部署与下载 | [07 部署访问和源码](documents/07-deployment-download.md) |
| 社区 | [08 当前功能](documents/08-community-features.md)、[09 内容规范](documents/09-community-guidelines.md) |
| 管理与知识 | [10 管理员说明](documents/10-admin-knowledge.md) |

[manifest.json](manifest.json) 保存逻辑 ID、分类、受众、版本和事实来源。`backend:` 表示本仓库，`frontend:` 表示独立前端仓库；这些是源码定位标识，不是可点击用户入口。每个业务小节标明当前功能、推荐规范或二者结合。推荐规范不构成审核时限、功能接入或生成无损保证。

[规划附录](planning/product-specification.md) 只作产品讨论，不在注册表内、不导入公共客服。它不承诺团队协作、自动内容审核或多库路由已实现。

## 使用说明页面

独立前端 `src/pages/DocsView.vue` 同步扩充普通用户使用说明；页面沿用既有路由与布局。当前下载/部署仅创建者可用，聊天 Markdown 导出允许创建者或管理员。页面按钮是否显示不能替代后端权限规则。

## 评测数据与人工复核

- [dataset.json](evaluation/dataset.json)：兼容现有 `customer-service-rag-eval/v1`，固定 90 条。逻辑文档 ID 用于维护内容，不是服务端真实 ID。
- [annotations.json](evaluation/annotations.json)：每条的分类、问法、关键事实、来源小节、禁止结论和审核状态。当前已由代理对照源码，仍需人工产品复核，未伪装成人工审核。
- [document-id-map.example.json](evaluation/document-id-map.example.json)：空映射示例，禁止拿假 ID 运行真实评测。

配额为平台 8、生成 24、预览 10、部署下载 14、社区 14、管理知识 5、边界安全 15。最后 15 条中有 6 条可回答的边界问题和 9 条真正的无答案/安全问题，不把“不支持某规划功能”一律当作拒答。每次更新保持总数 90，替换低价值重复样例并递增 datasetVersion；同步更新标注版本。

评测标注不是人工评审结果。人工抽检需记录条目 ID、答案要点满足情况、错误事实、漏步骤、状态混淆和是否通过。自动引用有效并不能证明回答事实正确。新版本适当分出调参样例与固定保留样例，避免反复用全部 90 条调整阈值后宣称泛化质量。2026-11-16另增48题证据边界诊断集，独立维护，不改变本目录90题配额；新集也不冒充独立盲测集。

## 本地校验

在 `ai-service` 执行：

```powershell
uv run python -m ai_service.orchestration.product_knowledge_evaluation
uv run python -m ai_service.orchestration.product_knowledge_evaluation --output ../tmp/product-knowledge-validation.json
```

默认校验文档引用、小节锚点、问题唯一性和固定配额，并用当前 ETL 解析/切分 11 篇正文。它不调用 Embedding、聊天模型或 Milvus，不写知识索引。输出 `liveMetrics: null` 是明确边界，不代表 90 条真实问答通过。

原 `ai-service/tests/fixtures/customer_service_eval.json` 的 6 条退款/密码/配送合成数据保留为评测算法测试夹具；它与真实产品库没有对应关系，不能直接拿它当产品召回基线。首次真实 90 条评测形成产品基线。

## 上传和更新流程

1. 产品人员复核正文及标注，确认当前功能来自页面和接口，推荐方式标记清楚。
2. 用管理员知识库页面逐份上传 `documents/` 的 11 个 Markdown 文件，不上传 README、注册表、评测集或 planning。上传必须经过 Spring，不允许 Python 直接写 MySQL、OSS 或生成应用目录。
3. 更新已有手册时使用替换原文档功能。盘点此前手册，避免新旧互相矛盾；旧文件禁用后等待异步任务完成并验证，不随意删除无法恢复的资料。
4. 等每份文件成功索引，记录实际 `documentId`、`documentVersion`、内容 SHA-256 和状态。以后台 ACTIVE/任务成功为准，不以“上传成功”为准。
5. 在本地忽略目录 `tmp/` 保存映射文件，每个逻辑 ID 对应真实 ID 字符串、版本整数和正文 SHA-256。摘要可从本地校验报告获取，并与后台上传内容摘要核对。
6. 核对实际文档映射后运行真实评测，逐条人工抽检，再发布该内容版本。

单项映射结构如下，尖括号位置必须替换为实际值，不可原样使用：

```json
{
  "schemaVersion": "product-knowledge-document-map/v1",
  "documents": {
    "01-platform-account": {
      "documentId": "<实际Spring文档ID>",
      "documentVersion": 1,
      "sha256": "<实际正文SHA-256>"
    }
  }
}
```

所有 11 个注册文档均需映射，版本必须匹配上传后的真实版本。校验器只验证本地摘要，不能代替管理员核对服务器实际内容；问答过程中遇到已映射 ID 的版本不一致会记录失败。只写 YAML 状态不具备隔离保证，所以本期通过不导入规划正文防止混淆。

## 真实 RAG 评测

默认校验命令只读取本地文档、数据集和标注，不连接真实依赖。`--live` 会启动本机 FastAPI lifespan，
初始化模型、Embedding、Milvus 和重排器；如需评估已运行的 Python 服务，使用 HTTP 模式：

```powershell
uv run python -m ai_service.orchestration.product_knowledge_evaluation --http-base-url http://localhost:8000 --run-id $ragRunId --document-map ../tmp/product-document-map.json --trace-file ../tmp/customer-service-rag.jsonl --output ../tmp/product-rag-report.json
```

HTTP 模式只读取现有 `.env` 中的 `AI_SERVICE_INTERNAL_BEARER_TOKEN`，向客服回答接口发送问题；不会初始化本机 RAG lifespan、GPU owner 或第二次检索。它要求完整实际文档映射、唯一运行 ID、服务正在写入的 trace JSONL 和一个尚不存在的输出路径。启动服务前设置 `AI_SERVICE_RAG_DIAGNOSTICS_ENABLED=true` 和 `AI_SERVICE_RAG_DIAGNOSTICS_REQUEST_PREFIX=<runId>:`，并为 `customer_service.rag.trace` logger 配置 `FileHandler`。requestId 使用 `<runId>:<caseId>` 且不得超过 128 字符；评测器仅关联本次 HTTP 请求期间追加的唯一 trace。运行会调用真实回答模型并产生使用费用，每题默认 60 秒超时，可用 `--timeout` 调整。

HTTP 报告把原始检索、真实重排、阈值后、文档选择后和最终上下文的文档排名分开列在 `stageMetrics`，并保存数据集、文档版本和服务配置指纹。原始检索报告 `Recall@8`、`MRR@3`、`NDCG@3`；重排及后续候选阶段报告 `MRR@3`、`NDCG@3` 和 `Coverage@3`，其中 Coverage 表示 answerable 问题中文档去重后的前三名至少命中一个目标文档的比例，不把最多 3 项的重排结果称为 `Recall@8`。重排排名来自同请求 trace 的阈值前真实分数；disabled 或降级不作为真实 rerank 成绩。trace 缺失、重复、结构不完整、配置指纹变化或文档版本不符均产生诊断错误，对应阶段指标留空；成功响应和超时后最多等待 0.5 秒收集 trace，不重试 HTTP，未写完的 JSONL 尾行不会被跳过。

独立 `answerMetrics` 统计可回答题实际回答、安全拒答、旧严格引用（来源文档 ID 唯一）、HTTP 延迟平均/P95、错误和降级。故障题保持在比例分母中，HTTP 故障、超时和服务 fallback 不算正确拒答；等待 trace 的时间不计入 HTTP 延迟。报告保存完整 answer 和响应 sources，供全部 90 题人工核对 `expectedFacts`。HTTP 输出必须位于仓库已忽略的 `tmp/`，终端仅显示无逐题正文的摘要。评测器不自动配置或检查 ACL，Git 忽略也不提供访问隔离；运行者须在运行前手动配置并核对目录 ACL，仅允许评测人员访问。不提交 Git、不上传公共日志，复核后按本地保留策略清理。常规 trace 仍不记录答案、来源摘录、令牌或异常正文。

可用 `--case-ids ID ...` 做小样本采集诊断，报告会标记 `diagnosticExperiment=true`，不得作为 90 题基线；默认完整数据集为 90 题。人工复核正确率需另行统计，代码不使用模型自动评分替代产品判断。

目标参考：Recall@8 ≥ 0.85、MRR@3 ≥ 0.75、NDCG@3 ≥ 0.75、无答案准确率 ≥ 0.90、引用有效性 ≥ 0.98、人工抽检正确率 ≥ 0.85。15 条边界安全并非都为无答案，真正拒答只有 9 条，因此比例较粗，应连同错误条目数一起判断。

## 回滚与维护

内容有误时通过原文档替换恢复经审核版本，再等待索引终态并复测；不要直接操作 Milvus alias 或删除 collection 来修正文案。新功能需同步修改对应正文和评测条目，保持总数 90。规划落地前不得迁入当前说明；落地后补上实现来源并替换规划断言。

真实环境记录写入 `tmp/`，不提交日志、凭据、个人问答或运行报告。无法获得管理员会话、实际 ID 或外部依赖时应交付可导入内容和校验结果，明确说明索引和真实基线未执行。

## 分层评估流程与新增诊断集

新增数据位于[customer-service-boundaries-v2.json](../../../ai-service/evaluation/customer-service-boundaries-v2.json)，包含48题、24组边界及63条精确来源事实，扩展2026-11-13的32题并重新标注，另增16题。题目涵盖单文档多事实、权限/验证码、停止/并发、表述别名、社区状态、源码依赖、图片边界及服务异常。代理标注均待人工复核；精确span只是可追踪证据，未穷举所有等价来源，不能直接当作完整语义gold。

新增工具入口为`ai_service.orchestration.rag_snapshot_experiment`，默认离线校验；显式`--live`运行真实模型的只读公共正文快照，`--analyze`离线导出分组指标和Ragas常见字段。生产Milvus基线仍使用上文的同请求HTTP流程，不能用快照成绩替换。详细命令、额度、边界及真实结果见[2026-11-16评估交接](../../customer-service-rag-evaluation-handoff-20261116.md)。

评估采用“目标假设→人工复核数据→冻结快照/模型/提示→基线与单变量候选→分组分析→失败归因→回归门槛”的闭环。三元组分别关注上下文相关性、忠实度与答案相关性；另外保留必要事实完整性、拒答、安全、版本一致性、错误、延迟与成本。语义评审不能替代人工核对，错误/缺失不得算通过；旧v1分数保留纵向对照，不因候选引用变多而扩大允许集合刷分。

## 固定90题的重排开关对照与全量盲审

新增 [rubric-20261118-v1.json](evaluation/rubric-20261118-v1.json) 独立保存采集答案前的来源审计与独立复核，`rubricVersion=product-2026-11-18-source-audit-draft-2`。它保留原 `dataset.json` 的全部问题、81道可答/9道拒答，以及每题历史 `originalAnnotation`。当前157条必要事实包含130条公共产品事实、27条R1拒答协议事实，另有29条可选事实、159处逐字原文证据。七项非题干必答补充已移入可选并记录旧/新factId及理由；没有因答案内容重新标注。标注仍为 `agent-source-reviewed-human-pending`，`humanGold=false`，精确quote只证明可追踪性。

[special-boundaries-20261118-v1.json](evaluation/special-boundaries-20261118-v1.json) 另列SLA、错误前提、部分可答和条件不匹配各2题。它仅用于离线诊断，未获本轮付费运行授权；不改变原90题分母、R1提示或生产策略，尤其不把未完善的部分回答契约当成已实现能力。

新入口 `ai_service.orchestration.rag_rerank_experiment` 默认只做本地校验，所有原文quote的Unicode码点起止、正文hash、历史问题与标注身份均需一致。下列命令在 `ai-service` 目录执行：

```powershell
uv run python -m ai_service.orchestration.rag_rerank_experiment
# 可选：同时校验现有逻辑文档与真实业务ID、版本、正文hash映射；仍不联网。
uv run python -m ai_service.orchestration.rag_rerank_experiment --document-map ../tmp/product-document-map.json
```

只有以下显式命令会消费本轮授权，需在规格和质量审查通过后运行。Spring内部snapshot须可读且内容冻结；不要另启Python API或其他BGE owner。工具自行最小组装依赖，不启动worker，不写MySQL、Milvus、alias或GC。它先核对Spring权威publication、完整Milvus语料/向量、11文档映射和本地正式ETL内容；源文件名和逻辑ID不冒充真实chunk身份。BGE固定本地HF修订 `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e`，缓存不全或CUDA不可用即失败，不下载、不降级。

```powershell
uv run python -m ai_service.orchestration.rag_rerank_experiment --live --document-map ../tmp/product-document-map.json --output ../tmp/rag-verification-20261118/rerank-live
```

授权study固定为 `rag-rerank-20261118-first-round-v1`，锚点固定为仓库 `tmp/rag-verification-20261118/rerank-authorization.jsonl`，独占新建并绑定输出目录与规范hash。不要删除、覆盖锚点或换目录重启绕过授权；工具拒绝复用，新的study不自动获得相同额度。额度为90个query输入的32/32/26三次Embedding请求和最多180次回答，发送前flush/fsync预占，失败/取消同样占用，无自动重试、付费裁判或稳定性轮次。GPU及知识预检失败可能发生在创建授权锚点前，后续准备失败则保留已发生的账本、向量和未运行结果。

两组共享同一次真实Milvus Top8，关闭组保留向量顺序且不调用BGE，开启组使用预先真实BGE分数；全部检索/重排准备成功后才开始回答。两组都走正式 `CustomerServiceRagService` 的过滤、每文档1块/最多3篇、1200/3000字符、UTF-8 JSON预算、R1严格解析及引用检查，阈值关闭。并发固定1，每题交替组别先后顺序，模型及预算相同。正式disabled模式无需BGE，开启组发生降级记为故障。模型前后继续核对权威snapshot；变化或不可用即停止，已经发送的调用不返还额度。

运行前即保存180个 `not_run` 结果，三次连续供应商调用失败停止，故障和未运行不从分母删除。报告保存代码SHA/脏工作树及文件指纹、配置/数据/标注/查询向量hash、完整publication只读快照、索引/search参数、本地模型/tokenizer文件hash、packages/GPU、预热、每题候选/排序/配额/预算、实际完整消息/答案/来源、usage和供应商结束原因。共享检索、BGE、回答供应商耗时和服务回放耗时分开，不将其称为两次在线E2E；未冻结单价时只报告token和调用数。终态非complete时CLI退出码为2。原始产物留在忽略目录，不能提交或作为公共trace输出。

离线导出全180张匿名卡片及严格模板：

```powershell
uv run python -m ai_service.orchestration.rag_blind_review --report ../tmp/rag-verification-20261118/rerank-live/report.json --export ../tmp/rag-verification-20261118/blind-review
# 将模板复制为实际评审文件，保持全部180项。给盲审人cards.md/cards.json及模板，私有映射单独保管。
Copy-Item ../tmp/rag-verification-20261118/blind-review/reviews-template.json ../tmp/rag-verification-20261118/blind-review/reviews-human.json
# 填写实际reviewer和role，再导入；允许pending，但缺项、重复、未知fact、伪证据与隐式类型转换会拒绝。
uv run python -m ai_service.orchestration.rag_blind_review --report ../tmp/rag-verification-20261118/rerank-live/report.json --package ../tmp/rag-verification-20261118/blind-review --reviews ../tmp/rag-verification-20261118/blind-review/reviews-human.json --output ../tmp/rag-verification-20261118/analysis-human.json
```

公共卡片随机打乱并隐藏arm、分数、调用顺序与耗时；`private-mapping.json` 才保存组别映射和seed。卡片提供问题、冻结必要/可选事实、支持源、实际最终上下文和回答/引用/故障。人工尚未填写时所有判定为pending；代理初审用独立文件并写 `reviewer.role=agent`，不会产生人工总体提升结论。软件校验身份声明和证据可追踪性，不能认证填写人身份，也不能证明其语义判断正确或claim已穷尽。

模板结构如下（单卡示意，实际文件必须保留全部180项，ID和原文从真实卡片复制）：

```json
{
  "schemaVersion": "rag-blind-reviews/v1",
  "studyId": "rag-rerank-20261118-first-round-v1",
  "publicPackageSha256": "保留模板生成的hash",
  "reviewer": {"name": "实际评审人", "role": "human"},
  "reviews": [{
    "blindId": "保留真实blindId",
    "status": "complete",
    "facts": [{"factId": "保留真实factId", "verdict": "met", "answerQuote": "逐字答案片段", "notes": "语义核对说明"}],
    "claims": [{"text": "逐字答案片段", "supported": true, "evidence": [{"chunkId": "真实输入chunkId", "quote": "实际上下文原文"}]}],
    "claimsComplete": true,
    "citations": [{"chunkId": "真实被引用chunkId", "supported": true, "claimTexts": ["逐字答案片段"]}],
    "answerRelevance": 2,
    "factualError": false,
    "safetyViolation": false,
    "annotationDispute": false,
    "failureCategories": [],
    "notes": "人工判断说明"
  }]
}
```

fact的verdict只允许pending/met/missing/contradicted；完整评审不能有pending。公共fact判met需提供逐字答案摘录，支持claim的证据只能来自实际contexts，不能用旁列的公共全文冒充模型输入。9道拒答的协议fact不要求空答案quote，按成功响应的answered=false、空来源及无安全违规分别核对。正常 `NO_ANSWER_TEXT` 与 `SYSTEM_FALLBACK_TEXT` 故障分开，不能把fallback算正确拒答。引用逐条登记支持的claim，未支持项不附虚构证据。

主通过率要求必要事实满足、没有错误/无依据claim、引用支持且相关性完整；故障/未运行按失败保留在每组90分母。公共必要事实覆盖仅统计81题的130条公共fact，拒答单列9题协议。报告同时保留误拒、故障、claim/引用、token/耗时及人工失败分类：未召回、排序截断、配额/预算丢失、证据在场生成错、标注疑义。自动“标注支持片段存活”先同等压缩空白，但整段quote未存活不能证明必要事实丢失，最终归因仍由盲审人判定。全部人工审完才生成总体人工提升值；未填项保留pending。配对四格和固定seed=20261118的10000次题级bootstrap 95%差值区间不把两组回答当独立样本；这90题仍是反复使用的开发回归集。

### 2026-11-18 首轮结果与新产品口径

本轮固定授权已使用：180 次回答、90 个 query Embedding 输入/3 请求。原冻结标注下，全量代理盲审为关闭重排 63/90、开启 74/90；人工验收按用户安排保留待办。脱敏聚合及题级配对区间见 [首轮结果](evaluation/results/rerank-20261118-v1.json)，失败与离线/真实验证范围见[逐轮交接](../../customer-service-rag-rounds-handoff.md)。原始答案、上下文和私有映射保存在本机忽略目录，不提交。

生成和盲审冻结后，用户进一步确认当前平台没有支付功能，应直接回答“目前平台不支持支付功能。”。该澄清单独记录于 [支付口径修订](evaluation/payment-clarification-20261118-v1.json)，两组旧答案按新口径均未直接说明当前状态；后置复核不覆盖原始成绩。短知识候选尚未发布，原 11 篇语料、90 题及本轮 rubric 保持冻结；新语料版本、INDEX 与真实验证需分别安排。

### 2026-11-18 下一轮 cap1/cap2 离线证据验证

使用上述首轮冻结的开启重排候选，只比较单文档一块/两块，最终仍最多三块及相同字符/字节预算。cap1 精确复现原 90 组最终上下文；三份匿名代理审阅覆盖两组全部 130 项公共必要事实。原匿名初审完整支持为 105/130 对 114/130；质量审查及两组定向复核纠正两事实、三条过严判定后为 cap1 106/130、cap2 116/130，原 review/初始汇总与独立修正侧车均保留。题内全部事实有完整支持仍为 67/81、72/81；这些是**上下文证据覆盖，不是新答案正确率**，人工验收仍待办。

cap2 改变 65 题选块，审后新增完整支持 10 项，本集未观察到原完整事实支持丢失，但累计挤掉 59 个文档出现次数，仍有 14 项未完整支持。旧 quota 归因的 15 项中，只有 7 项为本轮新增完整支持，另 1 项在本轮 cap1 已有语义依据，历史归因单独保留。组件测试本轮 25/25 通过，未新增聊天、Embedding、BGE 推理或生产写入，未改原授权账本或生产 cap1。方法、历史差异及下一轮边界见[第十三轮交接](../../customer-service-rag-rounds-handoff.md)，脱敏聚合见[配额证据结果](evaluation/results/cap-evidence-20261118-v1.json)。该轮三端来源结构按 chunkId 表达来源，仅源码核对，尚未完成该轮多块来源集成/浏览器验收。

### 2026-11-18 第十四轮：可回退配额与来源契约

正式服务增加 `AI_SERVICE_RAG_DOCUMENT_CHUNK_LIMIT`，默认 1、可显式设为 2，仅允许整数 1/2。启动时读取，改回 1 并重启可回退。按稳定排序保留同文档最多配置数量，每块占最终名额，总计仍最多三块；1200/3000 字符、实际 JSON 字节预算、活动版本及严格引用检查不变。固定 cap1 的两个旧真实实验入口在供应商及授权初始化前拒绝 cap2。真实 `.env` 未改，本轮未启用或部署 cap2。

本轮正式 `CustomerServiceRagService` 用冻结候选/BGE 顺序及 Fake Answer Model 回放，cap1/cap2 各 90 组上下文与第十三轮逐字一致，180 组逆序引用身份全部匹配；**180 次为 Fake 调用，不是新真实答案**。Python 完整 1753 通过/25 跳过，Java 干净编译及最终 36 项本地 HTTP Client/Service 测试通过（本轮新增 16 项，初版 31 项证据保留），前端新增 4 项及相关纯函数合计 48 项通过，类型检查和构建通过。前端测试使用保留三项用户原有修改的工作树；未执行真实 Spring/Python 联调或浏览器验收。临时回放首试的 Windows asyncio 本机通道审计问题及修正日志单列保留。

新增真实聊天、Embedding、BGE 推理、INDEX 和生产写入均为 0；原授权账本、历史报告和用户既有修改均保持。上段第十三轮的证据覆盖收益仍属历史代理判断，本轮不产生新答案质量成绩。cap2 仍可能挤掉跨文档依据，下一轮需要单列真实回答额度验证；人工验收及支付新口径语料发布继续待办。配置、失败记录、三端覆盖边界与提交见[第十四轮交接](../../customer-service-rag-rounds-handoff.md)，脱敏结果见[配额开关验证](evaluation/results/cap-rollout-20261118-v1.json)。
