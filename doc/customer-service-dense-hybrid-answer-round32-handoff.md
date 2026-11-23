# 第32轮：dense／hybrid同期回答对照（提前停止）

日期：2026-11-23。用户明确选择固定语料、选择策略、提示和模型，加入未参与调试题，比较答案完整性、证据支持、错误断言和拒答。本轮已执行，但未完成原定全部样本；不能据此证明混合检索稳定提升回答效果，也未推广生产。

## 固定条件与计划

沿用第27轮63块问答关联语料。dense为本地f32 COSINE穷举Top8；hybrid为dense Top8与中文双字/ASCII BM25 Top8经等权RRF(k=60)融合Top8。两组相同CUDA BGE、每文档cap1、最多三块、单块1200/总3000字符、16384提示JSON字节；未加入cap2、追加第四段、合块或新提示。

90道旧题复用经固定摘要核验的第27轮最终上下文，所有回答均在本轮重新请求，未将旧答案充当同期基线。另在首次新查询和回答之前提交24道新题（18道公共信息、6道拒答）及事实引文。新题由见过旧结果的代理编写，是新编未调试集，不是外部独立题或人工金标；与旧90题精确去重，字符SequenceMatcher最近题相似度最高0.5，这不证明语义独立。新题本轮已执行检索，后续不得重新称为完全未接触的独立集。

固定请求模型`deepseek-chat`、temperature=0.1、max_tokens=8192、text响应格式及正式提示/预算/JSON解析。实际本地窗口配置200000、协议预留4096，不是仓库默认值。48份HTTP响应均自报`deepseek-flash`，自报名称不认证底层模型版本。历史文档向量和新查询模型名称/维度相同，不能认证供应商Embedding版本没有漂移。

原计划90+24题、每题两臂共228次Chat，同题相邻、先后交替、单并发。新题使用1批/24查询Embedding、48次本地BGE；旧题不重新Embedding/BGE。顺序是先旧90题、再新24题，这一安排在首错停止时造成新题完全未到达，是本轮设计的实际不足。

## 真实执行与停止原因

独立固定目录`tmp/rag-dense-hybrid-answer-20261123`，排他账本，发送前flush/fsync预占。失败/取消占额度，零重试、首错停止、不续跑、不换账本。计划额度是上限，不能把未用额度解释为可以突破已冻结的停止规则。

新题检索49项预占（1 Embedding、48 BGE）全部完成。Chat第48次`app-generation-016:dense`返回HTTP200、finish_reason=stop，但输出中`citedChunkIds`键缺少开头双引号，JSON正式解析报`ANSWER_CONTRACT_INVALID`。没有手工修补后计成功，没有协议降级或补发。最终是47份有效回答、1份协议失败、180份未运行。

| 计划题组 | dense | hybrid |
|---|---|---|
| 旧90题 | 23有效／1失败／66未运行 | 24有效／0失败／66未运行 |
| 新24题 | 0有效／24未运行 | 0有效／24未运行 |

23道旧公共信息题构成完整配对；第24道旧题只有hybrid有效，保留但不混入配对差值。旧9道应拒答题、新6道应拒答题均未生成回答。旧题“客服能否查私人进度”“是否能下载私有原文”等已完成题是公共能力边界题，不能冒充真正应拒答测试。

实际1次Embedding/24查询、48次本地BGE、48次Chat发送；供应商Chat usage为prompt 36072、completion 3812、total 39884 tokens，含失败调用。Embedding报告902 tokens。Chat调用耗时累计50029ms，不含输入校验、检索、BGE和落盘，不是在线E2E或精确账单。无INDEX/生产写入、Spring或Milvus请求，没有服务启停或部署。

## 匿名审阅、缺项校正与结果

47份有效答案随机独立编号、打乱，卡片不带臂、执行序号、检索分数或cohort；映射另存。代理逐卡读取问题、冻结事实、答案、全部上下文及实际引用，转录事实完整性、实质断言支持、已证错误和是否适当回答。不是关键词自动评分。事实完整性采用严格二值，部分覆盖计未完整，理由注明部分；实质断言按整段合取组审阅，有一项不充分即整组支持不通过。摘录校验只是追溯性检查，不证明语义判断必然正确。

初评47卡完成后固定摘要，再读取映射统计。代理知道研究设计，而且此前已核查失败题及其臂身份，可能推测其唯一有效对照；不能称完全盲评、独立代理评审或真人双盲。真人材料仍是47卡pending。

本轮评审工具有一处缺项：初版匿名卡只投影事实正文和证据，漏带旧rubric的等价表达与适用条件。按过严口径，活动单页HTML建议及优化提示填输入框两项被误记未完整。初评、封存摘要和初版卡均保留；解盲后依据运行前就存在的等价表达另存两项校正，不改事实标签或源rubric，不将校正称盲评。工具已补全投影并加回归断言；只改评审工具/测试，发生在真实调用结束及86项运行完整性核验之后，没有重发请求。

| 23对旧开发题指标 | dense | hybrid | 口径 |
|---|---|---|---|
| 初版匿名审阅：全部必要事实完整 | 20/23 | 20/23 | 漏等价表达，保留作审计，不作为最终rubric评分 |
| 按原rubric校正后：全部必要事实完整 | 20/23 | 22/23 | 解盲后代理校正，真人pending |
| 校正后：完整事实项 | 32/38 | 35/38 | 描述性计数，事实不能当独立样本 |
| 答案全部实质断言获上下文支持 | 22/23 | 23/23 | 与事实覆盖不同 |
| 答案全部实质断言获实际引用集合支持 | 22/23 | 23/23 | 引用无逐句绑定 |
| 观察到已证错误断言 | 0/23 | 0/23 | 另有dense一题证据不足，零观察不证明无幻觉 |
| 公共信息题误拒答 | 0/23 | 0/23 | 不能替代应拒答测试 |
| 新题／应拒答质量 | 未测 | 未测 | 无答案，不能填成0%或100% |

校正后完整性配对差为+8.70个百分点，问题级10000次bootstrap区间[0,+21.74]个百分点，两题hybrid更好、零题dense更好，精确符号检验p=0.5；区间包含0。问题均值的事实覆盖差同为+8.70个百分点，是按题计算的比例差，不是35/38减32/38。上下文及实际引用支持各+4.35个百分点，区间[0,+13.04]，只有一个差异题，符号p=1。七维均是探索性比较、未做多重校正；小样本、提前停止、早序题偏差和解盲校正比数值本身更限制结论。

未从原定分母删除失败和未运行。归档的`intentionToEvaluate`保留90/24题两臂全部状态、81/18道公共题及9/6道拒答题。旧公共题完整回答的可识别范围为dense 20/81至77/81、hybrid 23/81至80/81（含hybrid未配对有效题）；新公共题两组均[0,1]。这是把未运行视为未知的范围，不是正确率或收益估计，无法据完整配对的局部比率推断计划全体。

### 具体变化与风险

- `app-generation-008`：dense只有页面/导航等描述，缺组件、状态及Vue建议；hybrid实际引用Vue段，补齐两项事实，是真实局部完整性增益。
- `app-generation-006`：dense只给布局/文字/移动端建议；hybrid明确活动单页适合HTML、一个主页面和少量交互，符合旧rubric等价表达，补齐一项。不能因没逐字说“完整HTML”判缺失。
- `app-generation-012`：hybrid虽未重复三种类型，但明确优化提示只先填输入框，满足旧等价表达；不应错记为退步。
- `app-generation-005`：两组均偏向部署/构建区别，HTML单文档和多文件拆分定义仍缺，Vue定义也不完整。检索增强没有解决全部已知遗漏。
- `app-generation-002`：dense在“创建不等于生成”之后，将无网页直接推成还需完成生成，证据不足以确认具体任务状态；hybrid未作该推断。归为支持不足，未确认真实状态相反，不能伪称已证事实错误。

## 冻结、验证和复现边界

设计及题集提交`3c976be`，实现提交`a32d866`，调用前消息冻结提交`ceefbcc`。canonical plan SHA256：`b0feaee0279ccff4c92731e785b077c47ddbe3e846a69824ee4efe5071006378`；canonical answer-plan SHA256：`b391105251ebf1b0ecd39fd50dd8ce18cc96c68aaa7aa00166031a029314a1e0`。文件字节摘要和canonical JSON摘要分开保存，不能混用。

真实运行结束核验86项绑定均未漂移；随后有意修正两份评审工具/测试，另外84项继续逐字一致。独立只读重放48组新检索/选择、180组旧最终上下文、全部228组消息及99行账本；原HTTP离线注入正式解析复现47有效和1失败，零新供应商调用。原HTTP、向量、账本和测试日志只留tmp，不提交。

定向16项通过，compileall和`uv lock --check --offline`通过。首次全量2616通过/1失败/26跳过，失败为既有30ms重排器恢复超时测试；当时存在并行真实评测，不能断言并行负载就是唯一原因。未修改该实现或测试；单独复跑通过，停止真实工作后的第二次全量2617通过/26跳过/0失败，2项依赖弃用警告。保留首次失败，不把复跑说成从未失败；未新增真实Milvus/PostgreSQL验收。

已执行的真实阶段命令如下，仅供追溯，不得重跑或换账本：

```powershell
cd ai-service
uv run --offline python -m ai_service.orchestration.rag_dense_hybrid_answer_experiment prepare
uv run --offline python -m ai_service.orchestration.rag_dense_hybrid_answer_experiment retrieve --plan-sha256 b0feaee0279ccff4c92731e785b077c47ddbe3e846a69824ee4efe5071006378
uv run --offline python -m ai_service.orchestration.rag_dense_hybrid_answer_experiment freeze-answers --plan-sha256 b0feaee0279ccff4c92731e785b077c47ddbe3e846a69824ee4efe5071006378
uv run --offline python -m ai_service.orchestration.rag_dense_hybrid_answer_experiment answer --plan-sha256 b0feaee0279ccff4c92731e785b077c47ddbe3e846a69824ee4efe5071006378 --answer-plan-sha256 b391105251ebf1b0ecd39fd50dd8ce18cc96c68aaa7aa00166031a029314a1e0
```

归档：[完整结构化观测](knowledge/product/evaluation/results/dense-hybrid-answer-validation-20261123-v1.json)、[调用前冻结](knowledge/product/evaluation/results/dense-hybrid-answer-20261123-freeze.json)。真人审阅时先只看[匿名卡](knowledge/product/evaluation/results/dense-hybrid-answer-blind-cards-20261123-v1.json)、[rubric补充](knowledge/product/evaluation/results/dense-hybrid-answer-rubric-addendum-20261123-v1.json)，另存填写[47卡模板](knowledge/product/evaluation/results/dense-hybrid-answer-human-review-20261123-v1.json)；评分固定前不要打开完整结构化观测，因为其中有解盲映射、代理评分和组别。

独立实验不验证生产Milvus ANN、权威publication快照、Java/前端/浏览器或端到端线上回答效果；现有未知状态人工处理、无自动GC等边界不变。用户原README围栏差异及取消回归计划保留，前端未改。

## 下一步

先做人类独立匿名复核现有47卡；当前只能报告开发题局部正向信号，不能宣布hybrid全面提高效果。若继续真实对照，应建立独立、显式新授权的后续研究，优先调度尚无答案的新题与拒答题，并在调用前决定协议错误作为观测保留后是否继续后续独立题。不能事后改变本轮停止规则、修补失败输出或复用本账本剩余额度。

后续可复用本轮已冻结的新题上下文和原提示，减少Embedding/BGE；需要明确它们已被本轮检索观察过，不能重新称外部未见金标。若要检验泛化，另由独立人员提供不参与调试的题目更有说服力。本轮没有自动启动后续研究、切换json_object、修改提示或改变生产选择策略。
