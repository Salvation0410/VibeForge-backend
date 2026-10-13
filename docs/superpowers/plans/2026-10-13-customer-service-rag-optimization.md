# 客服 RAG 链路优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本计划采用当前会话顺序执行；未经用户确认不进入实现。

**Goal:** 在固定 90 条产品评测集上补齐同请求证据，依据证据减少可回答题误拒答、提升回答证据质量，并保持权限、安全拒答和公共接口兼容。

**Architecture:** 保留 Spring 的业务权限、知识上传及版本管理边界，在 Python 既有客服问答链路中加入默认关闭的请求级诊断。先在不改变回答策略的版本上建立同请求基线，再比较有限的候选保留策略；正文、阈值和标注调整分别作为有证据才执行的分支。

**Tech Stack:** Python 3.12、FastAPI、Pydantic Settings、现有 Milvus KnowledgeStore、BGE Reranker、OpenAI 兼容回答模型、pytest、uv。

---

## 1. 状态、依据与授权边界

日期：2026-10-13。状态：**用户已选择方案 A 并确认执行；正在实施默认关闭的诊断和同请求评测工具**。

已阅读并对照：根目录 `AGENTS.md`、`ai-service/README.md`、`doc/customer-service-rag-optimization-handoff-20261013.md`、现有 RAG/提示/重排/接口/评测实现及对应测试。

本轮已确认本机六份交接证据文件均存在，解析了 `tmp/product-rag-http-20261013.json` 的聚合字段与失败题 ID；尚未逐条完成 38 条引用内容复核，也未连接运行服务核验当前索引或运行配置。下列数字是历史基线，不是本轮新评测。

用户已确认本计划；执行范围以 Python 为主，不预设修改 Java、前端、ETL 分块、生产索引或知识正文。当前工作分支为 `codex/rag-request-diagnostics`，保留用户 `projects/` 文件。

生产文档禁用/删除、alias 切换、容器/备份清理和中断用户服务不在默认执行范围；若新代码必须通过重启才能载入，先明确服务启动方式、所有权及重启窗口，再取得该操作授权。真实评测使用现有 HTTP 服务，不并行初始化第二个本地 GPU owner。不得提交 `.env`、`tmp/`、运行记录或用户既有 `projects/`。

## 2. 已核对的基线与关键事实

| 指标 | 历史结果 | 本次解释 |
| --- | ---: | --- |
| 评测版本/规模 | product-2026-10-12-v1；90 题 | 81 可回答、9 无答案/安全，题目及分类配额固定 |
| 可回答实际回答 | 72/81，88.89% | 9 条误拒答；管理知识仅回答 1/5 |
| 无答案正确拒答 | 9/9，100% | 原 ≥90% 目标在该集合要求 9/9 |
| 独立检索 Recall@8 / MRR@3 / NDCG@3 | 96.30% / 89.51% / 91.25% | HTTP 回答后另一次检索，不能视为同请求或重排成绩 |
| 严格样例级引用通过 | 43/90，47.78% | 9 条误拒答、38 条引用允许集不匹配；不等于 38 条伪造引用 |
| HTTP 平均 / P95 | 2.58 / 3.19 秒 | 历史参考；不是当前延迟验收阈值 |
| 请求/检索错误、降级 | 均为 0 | 后续故障必须单列，不能当作成功拒答 |
| 同请求重排指标、人工事实正确率 | 未测 | 后续分别采集，不以 Agent 或模型复核冒充人工 |

源码已证实的行为：

1. `_filter_and_dedupe` 先过滤无效版本，再按 chunk ID 和 document ID 去重；同文档只留首个有效块。
2. Reranker 当前仅返回最多 3 个块；字符预算是每块 1200、总计 3000，最终 JSON 还做 UTF-8 字节预算裁剪。
3. 路由收到 `requestId`，但 `_run_customer_service_answer` 当前只把问题传给 `service.answer`，未贯通请求诊断。
4. `rag_min_rerank_score` 源码默认为空；运行配置是否启用尚未核验，不能先认定阈值过高。
5. 现有 live 评测用应用生命周期启动依赖，且重排记录为空时回填原始排名；这只能是降级排名，不能标成真实重排结果。
6. 现有严格引用检查要求来源文档 ID 唯一。若将来允许同文档多个块作为引用，不能静默改变旧评测定义。

重点题集合：

- 9 条误拒答：`app-generation-004`，`deployment-download-003/009/012`，`admin-knowledge-001/002/003/005`，`boundaries-safety-004`。
- 3 条独立召回覆盖不足：`app-generation-006/008`、`boundaries-safety-004`。
- 两组相交一题，所以诊断集合是 **11 个唯一题 ID**，不是 12 个；报告保留“误拒答”和“覆盖不足”两个标签。
- `boundaries-safety-004` 是可回答的功能边界题，不改为无答案题。

## 3. 方案比较与推荐

| 方案 | 优点 | 局限/代价 | 决策 |
| --- | --- | --- | --- |
| A：先加同请求追踪，再按证据局部修复 | 能定位丢块、裁剪、模型拒答或引用失败；便于回退 | 增加受控诊断和评测整理工作 | **推荐，本计划主线** |
| B：直接增加 top-k、上下文和放宽阈值 | 改动直观 | 同时改变多个因素，不能定因；可能损害安全和延迟 | 本轮不直接采用 |
| C：重建知识结构、混合检索/多库路由 | 可能适合后续更大知识规模 | 架构、数据和运维范围明显扩大，现有证据不足 | 后续独立立项 |

方案 A 的首选修复实验是：有效块只按 chunk ID 去重，全部候选重排，再按文档保留分数最高的一个块，最后取最多 3 份文档。它修复“同文档更相关块无法参与重排”的可能问题，并维持当前来源文档唯一性及预算。

此实验以任务 3 的证据为前提，不预先认定有效。同文档多个互补块、提高召回量、调整预算/阈值或改正文均是条件分支，不同时投入主实验。

## 4. 文件边界

路径均相对后端仓库 `D:/VibeForge/yu-ai-code-mother`。

| 文件 | 责任/改动条件 |
| --- | --- |
| 新建 `ai-service/src/ai_service/orchestration/customer_service_trace.py` | 请求内诊断对象、稳定原因、脱敏序列化与日志输出；不存问答正文 |
| 修改 `ai-service/src/ai_service/orchestration/customer_service_rag.py` | 逐阶段记录；证据成立后调整候选保留与重排后选择 |
| 修改 `ai-service/src/ai_service/api/routes.py` | 请求 ID 关联、受控开启、超时/断连终态，公共响应不增诊断字段 |
| 修改 `ai-service/src/ai_service/config.py`、`ai-service/.env.example` | 默认关闭的诊断开关和受控请求前缀；示例只写非敏感值 |
| 修改 `ai-service/src/ai_service/orchestration/product_knowledge_evaluation.py` | 增加现有 HTTP 服务评测模式、关联诊断、独立运行标识；保留离线入口 |
| 新建 `ai-service/tests/test_customer_service_trace.py` | 默认关闭、隔离、阶段原因、脱敏、日志失败不改变问答 |
| 修改 `ai-service/tests/test_customer_service_rag.py`、`test_api.py`、`test_product_knowledge_evaluation.py`、`test_gateway_and_config.py` | 诊断契约、排序、预算、失败分类和配置测试 |
| 修改 `ai-service/README.md`、`doc/knowledge/product/README.md` | 仅说明已实现的诊断/评测模式与运行约束 |
| 条件修改 `ai-service/src/ai_service/prompts/customer_service.py` | 仅在预算/提示问题证实后改裁剪或边界表达 |
| 条件修改 `doc/knowledge/product/documents/*.md`、`manifest.json` | 仅在正文事实或操作小节缺陷证实后修改，通过 Spring 更新 |
| 条件修改 `doc/knowledge/product/evaluation/dataset.json`、`annotations.json` | 仅按来源内容复核结论版本化；90 题与可回答性保持不变 |

`customer_service_evaluation.py` 的旧口径默认保持不变。`models/reranker.py`、`infrastructure/milvus_knowledge.py` 先检查与测试，未发现其实现缺陷时不修改。若改共享签名，检索所有服务调用、API fake 和 live 包装器同步适配。

## 5. 分任务执行

### 任务 1：保留证据，核验运行环境

- [ ] 检查 Git 状态，记录当前提交及用户既有文件；执行阶段按 worktree 技能检查是否需要隔离，不能直接假设新 checkout 的 `.env` 或证据已就绪。
- [ ] 为历史 JSON、报告、映射和盘点计算 SHA-256；新运行使用 UTC 时间戳加随机后缀，创建 `tmp/rag-optimization/<runId>/`，禁止覆盖旧路径。
- [ ] 解析全部 90 题，重算误拒答、允许来源不匹配和覆盖不足集合，保存逐题基线；保留来源与版本，不只保存聚合数字。
- [ ] 读取健康接口及只读索引状态，核验 alias、文档版本/摘要和旧手册状态。只输出 top-k、预算、阈值、模型/重排版本等必要非敏感配置；不打印完整 Settings 或 `.env`。
- [ ] 对无法核验项注明历史快照/未验证。运行状态或文档摘要不同则先记录差异，后续建立当前基线，不把差异归功于优化。

**产物：** 运行前检查表、受保护历史证据摘要、新运行目录和非敏感配置快照。此任务不写索引，不调用回答模型。

### 任务 2：添加默认关闭的请求级追踪

**文件：** `customer_service_trace.py`、`customer_service_rag.py`、`api/routes.py`、`config.py`、`.env.example` 和对应测试。

- [ ] 先补 Fake 测试：关闭时不输出；前缀不匹配不输出；两个交错并发请求的块和原因不串题；恶意 requestId 按 JSON 编码不能注入日志。
- [ ] 新增配置 `rag_diagnostics_enabled=False`、`rag_diagnostics_request_prefix=""`。只有开启且前缀非空、请求 ID 匹配时才构建追踪；关闭时保持既有调用方式兼容。
- [ ] 路由为每次受控请求创建独立 trace，通过 `_run_customer_service_answer` 传入 `answer(question, *, trace=None)`。所有中间状态留在局部对象，不写服务实例字段、不做全局 requestId 缓存。
- [ ] 按下面契约记录排序、过滤原因和裁剪前后长度。每阶段最多 100 个候选，数值必须有限；长度只记录整数，不记录向量、问题、正文、模型答案、完整提示或异常文本。

```text
schemaVersion = customer-service-rag-trace/v1
requestId, traceId, configFingerprint, stageDurationsMs
retrieved: documentId, documentVersion, chunkId, rank, score
filtered: 上述身份 + kept / inactive / stale_version / invalid_metadata /
          duplicate_chunk / duplicate_document
reranked: identity, rank, score, scoreSource（reranker 或 retrieval_fallback）
thresholded / selected: identity, rank, kept, dropReason
characterBudget / byteBudget: chunkId, beforeChars, afterChars, afterBytes
model: called, answered（未调用时为 null）
citations: passed（未校验时为 null）, citedChunkIds
terminal: outcome（answered / refused / service_error / cancelled）,
          reasonCode, degraded
```

- [ ] 明确终态原因至少包含：`RAW_RETRIEVAL_EMPTY`、`FILTERED_EMPTY`、`RERANK_EMPTY`、`THRESHOLD_EMPTY`、`RERANK_UNAVAILABLE_WITH_THRESHOLD`、`CHAR_CONTEXT_EMPTY`、`PROMPT_BUDGET_EXHAUSTED`、`BYTE_CONTEXT_EMPTY`、`MODEL_REFUSED`、`MODEL_CALL_FAILED`、`MODEL_PARSE_FAILED`、`CITATION_INVALID`、`ANSWERED`、`TIMEOUT`、`CLIENT_DISCONNECTED`、`CANCELLED`，并记录 embedding/store 稳定服务错误码。
- [ ] 检查模型适配器的结构化输出异常类型，依已有类型区分调用与解析失败；若底层没有可靠分类，则记录 `MODEL_CALL_OR_PARSE_FAILED`，明确未能细分，不猜测原因。
- [ ] RAG 记录阶段状态，路由在 finally 统一输出一次诊断终态；直接服务调用由调用方负责输出。超时/取消不吞掉 `CancelledError`，保持现有 drain 行为。
- [ ] 通过专用标准 logger 输出一条 JSON 记录；日志采集配置把它导出到本机忽略目录，禁用传播以免混入普通业务日志。若未配置采集或日志写出失败，回答结果保持原样，评测则标记诊断缺失。
- [ ] 增补空召回、无效版本、重排降级/disabled、阈值、两层预算、模型拒答、伪造引用、超时、取消、日志输出失败的测试，断言公共响应无诊断字段和敏感内容。

**先运行红测再实现，随后验证：**

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother/ai-service
uv run pytest tests/test_customer_service_trace.py tests/test_customer_service_rag.py tests/test_api.py tests/test_gateway_and_config.py -q
```

**完成条件：** 现有回答策略不变；受控请求可关联全部经历阶段与唯一终态；关闭/非评测请求无诊断输出。

**提交：** `feat(rag): 增加受控请求级链路诊断`，同步提交该阶段 README 说明。

### 任务 3：构建同请求 HTTP 评测与定位表

**文件：** `product_knowledge_evaluation.py`、`test_product_knowledge_evaluation.py`、两个 README。

- [ ] 增加 `--http-base-url`、`--run-id`、`--trace-file`、`--case-ids` 参数；HTTP 模式和旧 `--live` 互斥，不初始化 Milvus/Embedding/Reranker。令牌从既有配置读取，HTTP 客户端不启用 debug 凭据日志。
- [ ] 默认只做本地校验；HTTP 模式要求显式模式、实际映射及独立输出路径。输出已存在则拒绝覆盖。问题数默认 90，子集报告必须标成诊断实验。
- [ ] requestId 采用 `<runId>:<caseId>` 并验证长度不超过 128；保留全量版本指纹。按 requestId 关联 trace，缺失、重复、不完整、配置不一致或文档版本不匹配都标记诊断错误。
- [ ] 用录制 HTTP/trace 测试鉴权读取、无二次检索、重复运行拒覆盖、错配轨迹拒用、服务兜底排除、故障和降级的指标空值。
- [ ] 首先运行 11 题诊断以检查采集；若首轮同时作为后续 90 题基线的一部分，应复用记录而非重复调用，配置/版本发生变化则不可拼接。
- [ ] 在原回答策略加诊断版本上完成 90 题同请求基线；将历史独立检索基线和新基线分别命名。
- [ ] 整理 11 题原因表：原始命中块、过滤丢失块、重排得分/排名、实际进入模型的片段、终态、对应 expectedFacts、已证实原因与未确定项。正文证据只从受控知识材料对照，不加入普通日志。
- [ ] 修复评测展示：原始排名、真实重排（阈值前）、阈值后、文档选择后、最终上下文分别报告；降级/disabled 只报告原顺序，不填真实重排成绩。

**拟实现后的命令契约：** 以下参数目前不存在，只有本任务完成后才可执行。

```powershell
uv run python -m ai_service.orchestration.product_knowledge_evaluation --http-base-url http://localhost:8000 --run-id $ragRunId --document-map ../tmp/product-document-map.json --trace-file $ragTraceFile --output $ragReportFile
```

`$ragRunId`、`$ragTraceFile`、`$ragReportFile` 由本次运行目录生成，禁止复用历史输出。日志在请求结束后允许有界等待采集，超时后记录缺失，不额外请求回答来补轨迹。

**完成条件：** 所有重点题获得原因证据或明确无法定位；重排空值不伪装成真实排名；90 题新基线完整，任何失败单列。

**提交：** `feat(rag): 支持同请求HTTP评测与阶段指标`。

### 任务 4：依据证据修复候选保留

**文件：** `customer_service_rag.py`、`test_customer_service_rag.py`、`test_reranker.py`、README。

**进入条件：** 任务 3 证明相关事实所在有效块在重排前因 document ID 重复被丢弃。如果并未证实，跳过该改动，按任务 5 对真实原因处理。

- [ ] 修改已有去重测试，把版本过滤、chunk 去重与文档选择分别断言；新增“首块弱相关、后块强相关”的红测。
- [ ] 有效候选仅按 chunk ID 去重，保留原始稳定顺序；调用现有重排器返回全部有效候选排名，`top_n=len(candidates)`，在已有最多 100 候选限制内执行。
- [ ] 先校验重排返回的 ID、有限分数和排序；分数并列时按原始召回顺序稳定排序。上下文与来源取经过校验的原候选对象，不能接受同 ID 的替换正文或版本。
- [ ] 保持阈值语义：只作用于真正重排分数；重排故障且启用阈值时继续保守拒答。disabled 和故障回退的完整候选保持召回顺序并标明 scoreSource。
- [ ] 阈值后按排名选每文档最高分的一个块，再取最多 3 份文档；保留来源文档唯一性、最大 3 来源、1200/3000 字符和既有字节预算。
- [ ] 诊断增加选择阶段，区分 `duplicate_document`、最终 top-k 淘汰与预算裁剪，不能把所有损失归为重排。
- [ ] 回归同文档后块胜出、重复块、失效/旧版本、稳定并列、多文档补位、坏重排输出、disabled/降级、引用校验与 prompt injection。
- [ ] 对 11 重点题和 9 安全题做一轮 20 题对照，仍只改候选保留这一因素；失败则保留诊断证据并回退该实验。

```powershell
uv run pytest tests/test_customer_service_rag.py tests/test_customer_service_trace.py tests/test_reranker.py tests/test_milvus_knowledge.py -q
```

**完成条件：** 代码层面关键后块可以参与重排，兼容/安全测试通过；真实收益以评测为准，不能只凭单测宣布误拒答已修复。

**提交：** `fix(rag): 将文档去重移至候选重排之后`。

### 任务 5：条件分支与 38 条来源复核

按证据选择，最多比较两组候选实验；每组只改变一种因素。未出现相应问题则不执行该分支。

| 已证实原因 | 处理动作 | 必须验证 |
| --- | --- | --- |
| 关键内容在字符/字节裁剪中丢失 | 首先评估完整业务小节；仅必要时调整预算分配 | 最终 JSON 字节上限、中文/转义文本、长问题、顺序公平性及安全题 |
| 同文档互补事实确需多个块 | 单独比较每文档最多 2 块、总计最多 3 块 | 块 ID 引用合法；旧严格口径保留；新增文档去重口径单列，避免重复文档被算作提升 |
| 阈值筛掉有用证据 | 先分析可回答/安全题分数分布，再做一组有限阈值对照 | 9/9 安全拒答、降级量纲、误拒答和延迟；不用 Milvus 分数代替重排分数 |
| 原始召回缺失 | 比较现有 top-k 8 与一次 top-k 16 的局部实验 | GPU 时延/限制、上下文保持同预算、安全题；不得同时改分块和 embedding |
| 正文缺少独立完整事实或新旧内容冲突 | 对照实现补齐业务小节，通过 Spring 替换/异步索引 | 上传权限、终态、实际映射、版本/摘要；不禁用旧手册来刷指标 |
| 上下文完整但模型拒答 | 检查功能边界题提示表达，明确“资料支持未实现”可作边界回答 | JSON/引用契约、禁止推测和 prompt injection；不加入题 ID 特判 |

- [ ] 逐条复核历史 38 题：实际来源、expectedFacts 覆盖、证据小节、错误事实/越权/规划混淆、支持/不支持/待确认、修正类别及审核身份。
- [ ] 真实错误进入正文/检索/提示修复；合理补充来源形成标注修订建议。不得只因为模型引用了某文档就扩大允许集。
- [ ] 标注确需修改时保留 v1 快照，递增 datasetVersion 并同步 annotations；题 ID、问题、可回答性及分类配额不变，同时报告旧允许集和修订允许集成绩。
- [ ] Agent 复核统一标为 Agent，人工字段保持待审；正文/标注涉及产品歧义时只提交明确建议，不自行确认产品能力。
- [ ] 正文修改与运行内容更新分开记录；缺少管理员会话时交付可导入正文及未更新状态，不绕过 Spring。

**产物：** 原因对应修复、单因素实验记录、38 题完整复核表、必要版本变更及回退说明。运行记录留 `tmp/`，可提交文档只记录非敏感方法与经确认结论。

**提交（按实际分支拆分）：** `fix(rag): 修复已证实的上下文证据损失`、`docs(rag): 补齐知识事实与来源复核说明`。不为未执行分支创建空提交。

### 任务 6：全量回归、人工验收与交付

- [ ] 最终候选执行同一 90 题全量 HTTP 回归；核对运行配置、源码提交、知识版本/摘要和标注版本，生成逐题新旧差异。
- [ ] 报告原始检索 Recall@8、MRR@3、NDCG@3；真实重排报告 MRR@3、NDCG@3 和 Coverage@3。若重排不足 8 项，不标成重排 Recall@8。
- [ ] 分别列出阈值/选择/字符/字节阶段的证据损失，可回答回答率、9 题误拒答变化、管理知识回答率、严格引用、来源内容支持性、平均/P95 延迟，以及故障/超时/降级。
- [ ] 输出全部 90 题人工验收表；建议人工检查全部题，至少覆盖错误、权限与安全。人工未完成时结论明确为“实现及自动回归完成，人工验收待办”。
- [ ] 更新 `ai-service/README.md`，仅写当前已实现能力；执行 Python 完整验证和离线产品校验。

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother/ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
uv run python -m ai_service.orchestration.product_knowledge_evaluation
Set-Location D:/VibeForge/yu-ai-code-mother
git diff --check
git status --short
git log -1 --pretty=%s
```

- [ ] 若证据要求改 Java 或前端，先记录新增范围及接口影响，按 `AGENTS.md` 补对应编译/测试；本计划不假定跨仓库检查已通过。
- [ ] 只暂存本任务文件并作中文 Conventional Commits；报告提交哈希、已执行命令和结果、真实评测路径、已证实原因、未测/未达标项和人工状态。

## 6. 验收标准与实验预算

沿用原设计目标：原始检索 Recall@8 ≥85%、MRR@3 ≥75%、NDCG@3 ≥75%；无答案正确拒答 ≥90%（本集合必须 9/9）；严格样例引用通过 ≥98%（90 题至少 89 题）；人工抽检正确率 ≥85%。自动引用字段有效不能替代人工事实正确。

本计划额外建议作为用户确认项：

- 旧 v1 口径可回答题回答数至少高于 72/81；9 条误拒答逐条交代是否修复和原因；管理知识至少高于 1/5。
- 安全 9/9 与既有权限/引用防护不退化；若旧严格引用未达 98%，如实交付未达标项，不能只切换标注口径宣布达标。
- 不设未经确认的硬性 P95 门槛，明确新旧差异及各阶段耗时；明显延迟回退需解释，不能省略。

模型调用预算建议：原策略同请求基线 90 题 + 每候选 20 题（11 重点 + 9 安全，最多 2 候选）+ 最终 90 题，共约 **200—220 次回答请求**；embedding/重排随请求发生，不再额外执行独立诊断检索。失败复跑和供应商重试单独计数；达到预算仍不能定位时先交付证据与下一步建议，不无限调参。金额取决于实际供应商和 token，计划不虚构费用。

整个固定集合已经用于历史评测，本轮结果只说明该集合回归效果，不宣称泛化。若后续要评估泛化，另行确认固定 90 题内部划分，不额外增题。

## 7. 回退与检查点

1. 诊断异常：关闭诊断开关；保留问答策略和受控日志证据，不删除数据。
2. 候选策略无收益或安全退化：回退本次候选选择提交/配置，保留追踪和 HTTP 评测工具；不用 Git reset 覆盖用户修改。
3. 预算/阈值实验：恢复有指纹记录的上一配置，按必要安全题确认。
4. 正文错误：通过 Spring 替换恢复经核验正文，等待异步终态并更新映射；不手改 alias。
5. 标注复核争议：继续保留 v1 主报告，修订口径和待人工项目分列。

执行检查点是任务 3 的原因表、任务 4/5 的单因素对照以及任务 6 的全量报告。除产品歧义、必要服务重启或超出授权范围的操作外，确认计划后按顺序自主推进，无需逐任务再次请求许可。

## 8. 本轮计划交付检查

- 已核对历史原始 JSON 聚合数字、9 条误拒答及 3 条覆盖不足题 ID，重点集合为 11 个唯一题。
- 已明确源码确认事实与待证实原因，未把当前配置默认值当作运行值。
- 已映射追踪、API、评测、候选保留、来源复核和全量验收到任务及文件。
- 本轮不运行真实模型评测、不改服务运行状态、不改生产知识/索引；仅检查计划链接、文件路径与 Git 空白错误。
- 用户确认这份计划后，再读取执行技能并开始任务 1。
