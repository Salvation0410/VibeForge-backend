# 客服失败原因观测与人工评测交接

## 第一轮：源码调查与待确认设计（2026-11-20）

状态：只读调查和方案交付；尚未实施代码、编写运行脚本或执行测试。本文独立记录本次观测工作，历史真实实验仍以 `customer-service-rag-rounds-handoff.md` 为准，不把历史成绩算成本轮验证。

### 问题与证据

用户要求继续完善失败原因观测，由 Agent 编写人工观测测试脚本，再根据结果决定是否补混合检索或调整证据选择。人工观测在本方案中指“生成可供人评阅的材料、导入实际人工填写、汇总并保留未评状态”；自动测试和 Agent 审阅不能冒充人工裁决。

核对以下当前源码：

- `ai-service/src/ai_service/orchestration/customer_service_trace.py` 已记录检索、过滤、重排、阈值、选择、字符/字节预算、模型与引用信息；诊断必须同时满足开关和请求前缀。日志只存白名单元数据，专用 logger 默认 NullHandler，不等于已部署持久采集。
- `customer_service_rag.py` 在权威读取作用域异常时调用 `finish(..., KNOWLEDGE_PUBLICATION_UNAVAILABLE)`，但 trace 原因白名单缺此值，实际会归并为 `CUSTOMER_SERVICE_UNAVAILABLE`。
- 同文件权威知识库为空的分支直接正常拒答，没有调用 `finish`，trace 会保留默认系统错误终态。
- 模型前后 `assert_current()` 与模型调用位于同一 try 中，快照复核异常可能记录为 `MODEL_CALL_FAILED`；权威读取外层捕获也可能覆盖原先更具体的 Embedding/检索诊断。需用故障注入验证后修正归因。
- `rag_evaluation_workflow.py` 已有按标注原文 span 衡量证据覆盖的能力；`rag_blind_review.py` 已有匿名卡、human/agent 身份和 pending 状态，不应另造一套互不兼容的质量分。
- 第十九轮交接明确：最终 context 缺必要事实，尚不能证明损失具体发生于召回、重排或配额阶段；人工验收仍 pending。这是历史限制，本轮未重放其原始数据。

### 可选方案与推荐

| 方案 | 收益 | 代价与限制 |
| --- | --- | --- |
| A：仅离线分析历史材料 | 不改变运行时，较快获得待评清单 | 无法补回未记录阶段，也不能解决当前终态误归因 |
| B：修正诊断归因并提供离线人工评测工具（推荐） | 同时改善工程故障定位和质量决策证据；复用现有 trace 与盲审 | 需修改小范围诊断代码并测试；语义质量仍需人评 |
| C：新增观测数据库、看板和线上采样 | 便于持续运营 | 新增存储、权限、保留策略和部署工作，当前没有必要先扩组件 |

推荐 B。本轮不改变回答、召回、重排、cap、预算、拒答、引用门禁或供应商重试策略。生产 text/cap1 及原始评测标注保持；不实施 GC、未知任务自动接管或恢复。

### 推荐设计：触发、判断、流程与模块

1. **工程故障观测**：保留现有显式诊断开关和前缀入口，修正知识库为空、权威读取不可用、模型前后复核失败的固定原因与阶段归属。已知阶段故障不能被外层通用捕获覆盖；未知异常使用固定兜底，不保存异常正文。取消、超时及原有返回协议保持。
2. **离线观测脚本**：在现有 orchestration 评测模块旁新增独立入口，读取显式指定的历史报告、阶段记录、冻结标注与评审材料，校验 schema、样本身份、版本及可用 hash。生成固定分母的工程原因汇总、逐题证据损失表及人工待评清单。输入缺失或不兼容应明确报错或标记不可判定，不能将“未观测”记为未召回。
3. **证据归因**：仅在对应阶段有完整记录且冻结标注可核对时，报告标注 span 在召回、过滤、阈值、选择、字符和字节裁剪间的存活变化。阶段缺失、截断或身份不符保留 UNKNOWN；最终缺失不直接归因 TopK。字面匹配是证据诊断，不等于语义充分或答案真实。
4. **人工材料**：复用匿名卡/严格导入，呈现问题、答案、实际上下文、必要事实与评审说明；代理判断与人工判断分开。脚本可以验证格式和汇总，但不能自动填写 human=complete。人工未填写时保持 pending，质量改善为 null；允许标注争议单列。
5. **报告输出**：每次新建输出目录，拒绝覆盖历史报告、账本和评审；详细原文只留本地忽略目录，交接仅提交必要脱敏聚合。统计服务错误、正常拒答、未运行和缺材料，保留各自分母，不用成功子集掩盖失败。

### 验收范围与故障矩阵

实施后新增 Fake 驱动测试：权威库为空、获取快照失败、模型前复核失败、模型后快照变化、Embedding/检索失败不被外层覆盖、模型调用/解析失败、引用非法、正常拒答、取消/超时、日志写入失败。断言业务结果不变、模型调用次数不增加、原因正确且日志无问题/答案/原文/异常内容。

脚本测试覆盖：完整与缺阶段材料、截断材料、重复样本、版本/hash 不匹配、预算裁剪、配额丢失、工程失败与拒答分母、未评 pending、agent 不成为 human、非法人工导入、历史文件拒绝覆盖。以本地 Fake 数据验证工具，再只读重放可验证的历史材料，分别记录结果。

在 `ai-service` 执行相关测试后，执行 `uv run --offline python -m compileall -q src`、`uv run --offline pytest`、`uv lock --check --offline`。本轮不安排新增真实 Chat/Embedding/BGE/INDEX、部署或生产写入；历史耗尽账本不能复用。若后续确需新真实样本，另列明确额度与范围。

### 本轮验证与交付状态

本轮只读核对代码、历史交接、Git 状态和 README 既有差异；没有运行测试、真实模型、服务或浏览器验收。未对用户当前环境的线上失败比例作结论。

既有 `ai-service/README.md` 围栏修改和未跟踪的 `docs/superpowers/plans/2026-11-11-cancellation-drain-regression.md` 保留；本轮仅提交本文。提交哈希在本轮回复报告。

### 下一轮与决策边界

用户确认方案 B 后，编写实施计划、诊断修改及脚本，执行测试与历史只读回放，同步 AI 服务 README、本文和原逐轮交接入口，按中文 Conventional Commits 提交。每轮回复说明：改动、原触发场景、为什么这样做、验证与限制、提交和下一步。

混合检索只在“必要证据在当前语料中存在但召回候选缺失”的可核对样本支持下再讨论；证据已召回却在配额或预算阶段丢失，优先研究选择策略；证据保留但答案遗漏/越界，需单独研究生成与评审；语料缺失或标注争议先处理知识与标注版本。这些是下一实验方向，不构成自动推广门槛。未知执行状态仍需人工处理，候选无自动 GC，引用校验不能消除幻觉。

## 第二轮：方案 B 实施与离线观测（2026-11-20）

用户已确认 B 方案。本轮对应原总交接的第二十轮；第一轮方案状态保留，不用实施结果覆盖此前“未运行测试”的记录。

### 改动、触发条件与处理流程

运行时：`customer_service_rag.py` 修正空权威库正常拒答的终态；`customer_service_trace.py` 提供共享固定原因集合。新增 `KNOWLEDGE_PUBLICATION_EMPTY`、`KNOWLEDGE_PUBLICATION_UNAVAILABLE`、`KNOWLEDGE_PUBLICATION_CHANGED`。变化码只接受本地 `MilvusKnowledgeError` 的精确固定值，任意供应商异常的同名属性不会被透传。

`terminal.phase` 为可选字段，白名单是 `publication_read`、`publication_before_model`、`publication_after_model`、`publication_scope_exit`。模型前后复核失败不再记为模型调用失败；同一已分类输入/Embedding/检索异常穿过作用域时保留原原因，退出阶段另发新异常则归到退出失败。原返回、fallback、调用次数、取消和重试策略保持。schema 仍为 v1，历史无 phase 记录在新工具中显示 UNOBSERVED。

诊断仍需显式 `AI_SERVICE_RAG_DIAGNOSTICS_ENABLED=true`，请求匹配非空 `AI_SERVICE_RAG_DIAGNOSTICS_REQUEST_PREFIX`，并由运维给 `customer_service.rag.trace` 配置 FileHandler。默认不开启，NullHandler 不存日志；本轮未更改 `.env` 或服务日志配置，也未重启服务，不能称线上采集已经部署。现有 HTTP 评测会调用真实模型，本轮未运行它。

离线入口：`ai_service.orchestration.rag_failure_observation`，证据核对模块为 `rag_evidence_observation.py`。仅支持现有固定 rerank、cap 和 JSON 回答三项研究，各 90 题×两组；不接收任意线上 JSONL 作为实验报告。它按报告原始字节 SHA256、冻结 rubric、完整样本集合核对输入；文档映射必须匹配报告中 `sourceFilesSha256['document-map.json']` 及冻结文档摘要。未提供映射时仍统计工程状态，证据阶段为 UNKNOWN。

各阶段用已捕获原文和 trace 的片段身份、文档版本、长度及 kept 集合交叉核对；过滤原文只从完整检索记录投影，不从最终答案反推。预算阶段允许前缀裁剪；阶段缺失、显式截断、达到 100 条元数据上限、重复身份、长度/版本不匹配保留 UNKNOWN。只报告经过当前标准文本清理的整段标注引文存活，不称语义证据充分性。相邻可观测阶段才能定位丢失；前段有缺口时不声称首次原因。选择丢失细分固定的 `duplicate_document`、`document_chunk_limit`、`top_k`，其余 UNKNOWN。

人工入口复用 `rag_blind_review` 的匿名卡、严格逐事实/断言/引用校验及统计，并再次核对卡片问题、答案、上下文和事实与原报告一致。所有卡保留，无自动填分；human 是评审人的身份声明而非软件认证。代理完成仍为 human pending；引用和引文校验也不能证明人工判断语义正确。

### 本轮历史材料观测结果

原材料为第十九轮 2026-11-19 的已落盘真实报告，本轮仅本地读取：原始 SHA256 `7fa940447c2553431974c342519ec233395932ca72d7b43b18a005149b325579`。本轮新增 Chat/Embedding/BGE/INDEX/生产写入均为 0。

| 项目 | text | json_object |
| --- | ---: | ---: |
| 固定样本分母 | 90 | 90 |
| 正常回答 / 正常拒答 | 76 / 12 | 78 / 12 |
| 工程错误 / 未运行 | 2 / 0 | 0 / 0 |
| 工程错误原因 | MODEL_PARSE_FAILED × 2 | 无 |
| 公共标注支持片段分母 | 130 | 130 |
| 最终仍有整段标注引文 | 96 | 96 |
| 已记录 Top8 无整段标注引文 | 10 | 10 |
| 选择阶段丢失整段标注引文 | 24 | 24 |

两组共享相同冻结输入，上述不是两批独立检索证据。选择阶段的 24 条均具有 `duplicate_document` 排除记录，字符/字节预算未发现新的整段标注引文首次丢失。七个阶段的 180 份记录都通过本工具核对；“OBSERVED”只代表捕获材料内部一致，不认证当年在线检索完整性、当前知识库或供应商身份。130 为支持片段/事实标注口径，不能把 96/130 称作真实答案正确率，也不能把 34 条引文未存活当成 34 个错误答案。

历史原人工模板导入仍 pending；原代理终审导入是 quality complete、human pending，人工改善为 null。没有新增人工评分、重做代理质量裁决或改旧标注。输入旧 report、map、评审、盲包、三份耗尽账本以及本地配置等 12 份保护材料在本次最终回放前后摘要相同；README 随后仅追加本轮说明，用户原围栏修改保留。

### 人工观测操作

已生成本轮新匿名包：`tmp/rag-failure-observation-20261120/final-observation/blind/cards.md`。逐卡读取问题、答案、实际上下文及必要事实，将同目录 `reviews-template.json` 复制为新的 `reviews-human.json`，填写实际评审人及逐项结果；暂未评的卡保持 pending，不删除。只把 cards 与模板交给盲审人，不交私有映射或带 arm 的诊断报告。

在 `ai-service` 运行以下命令可新建另一轮观测目录；已有目录会被拒绝。原始材料只在本机忽略目录，缺失时明确失败，不能从提交的聚合文件反构原文。

```powershell
uv run --offline python -m ai_service.orchestration.rag_failure_observation `
  --report ../tmp/rag-json-answer-20261119/prepared/live/report.json `
  --report-sha256 7fa940447c2553431974c342519ec233395932ca72d7b43b18a005149b325579 `
  --document-map ../tmp/rag-verification-20261118/live/document-map.json `
  --output ../tmp/rag-failure-observation-20261120/my-new-observation
```

实际填写完本轮包后导入（可保留未评卡，不要求一次评完 180 卡）：

```powershell
$reviewPath = '../tmp/rag-failure-observation-20261120/final-observation/blind/reviews-human.json'
$reviewDigest = (Get-FileHash -LiteralPath $reviewPath -Algorithm SHA256).Hash.ToLowerInvariant()
uv run --offline python -m ai_service.orchestration.rag_failure_observation `
  --report ../tmp/rag-json-answer-20261119/prepared/live/report.json `
  --report-sha256 7fa940447c2553431974c342519ec233395932ca72d7b43b18a005149b325579 `
  --document-map ../tmp/rag-verification-20261118/live/document-map.json `
  --package ../tmp/rag-failure-observation-20261120/final-observation/blind `
  --reviews $reviewPath --reviews-sha256 $reviewDigest `
  --output ../tmp/rag-failure-observation-20261120/my-human-review
```

输出 `observation.json` 保存逐题阶段诊断，`summary.json` 保存聚合，`review-analysis.json` 保存严格人工/代理统计，`README.md` 为快速说明。只允许新建仓库 tmp 子目录；输入单文件上限 64 MiB，拒绝重复 JSON 键和非有限数值，CLI 固定错误不回显输入正文。写入中断可能留下部分新目录，不保证文件事务；保留现场并换新的分析输出目录即可，本工具不发送真实请求，不涉及恢复模型额度。

### 验证与交付记录

运行时新增回归先复现 12 项失败，再修正；定向组合 75 项通过，原 RAG 加新增测试 83 项通过。离线工具红测先因模块未实现收集失败；初版 17 项中 1 项失败，原因是误把合成夹具原有 178 个错误当作 1 个，修正断言后通过，未修改统计来凑预期。规格审查另发现 cap>1 的固定配额码遗漏，补白名单与四组参数测试。

新两个测试文件的独立规格复验为 57 项通过。真正历史 CLI 回放含新包导出、旧人工模板/旧代理审阅导入，错误 hash 在输出前拒绝；导入及坏 hash 回放禁止 socket 连接，网络尝试为 0。未运行真实服务、Java/前端构建、浏览器或可选基础设施集成验收。完整回归 **2160 通过、25 跳过、0 失败/错误**；末次 JSON 指数溢出拒绝加固后，两个新增文件再运行 **57 通过**，独立审查另确认有限浮点兼容与正负溢出拒绝。两组计数不累加；完整回归收集后的这次小改动以定向测试验收。compileall 和 `uv lock --check --offline` 退出 0，锁解析 150 包；保留 Starlette BlockingPortal 和 LangGraph allowed_objects 两条既有弃用警告。原始日志留 `tmp/rag-failure-observation-20261120/`，脱敏汇总为 `doc/knowledge/product/evaluation/results/failure-observation-20261120-v1.json`。

保护校验脚本首次把 Windows 反斜杠路径和 POSIX 字面量比较，误把预期 README 追加当作保护文件变化；改为 resolved Path 比较后确认 11 份输入摘要不变、README 为原字节加本轮追加，未回滚或覆盖用户改动。代码/文档仅本轮定向提交，提交哈希由最终回复报告；未推送远端或部署。

实际验证命令（ai-service 目录）：

```powershell
uv run --offline pytest --junitxml=../tmp/rag-failure-observation-20261120/python-final.xml
uv run --offline pytest tests/test_rag_failure_observation.py tests/test_rag_failure_trace.py --junitxml=../tmp/rag-failure-observation-20261120/final-target.xml
uv run --offline python -m compileall -q src
uv lock --check --offline
uv run --offline python ../tmp/rag-failure-observation-20261120/verify-replay.py
```

最后一条为本轮现场回放脚本，仅首次执行成功；因输出拒绝覆盖，重复运行会失败，应保留原证据并按上述正式 CLI 换新目录执行。

### 下一轮建议、原因和仍可能失败的场景

先人工核对这 34 条未存活引文对应的题目，尤其 24 条被单文档配额排除的情况：整段引文可能只是太长，而保留片段已经足够；也可能原标注或问题范围有争议。若人工确认确有必要证据丢失，优先做独立的证据选择对照；当前证据还不足以直接增加混合检索。对 10 条 Top8 未包含的引文，再核对语料和标注版本，确认是真召回不足后才设计混合检索实验。

旧模型前后失败阶段无法补回，未知执行状态依然人工处理，候选没有自动 GC，当前最后快照复核后的撤销竞态仍在。本轮没有消除幻觉、认证语义正确性、自动恢复任务或变更生产 text/cap1。运行时代码变化也使旧真实实验计划的关键实现摘要失配；本轮只读回放不校验“旧实现等于新实现”，而校验旧材料自身身份。新真实实验仍需新计划和明确额度，不能复用耗尽账本或旧批准。
