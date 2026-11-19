# 客服 JSON 输出约束下一轮验收设计

日期：2026-11-19。阶段：第十八轮设施实现及离线验收完成；随后用户明确批准正式计划最多180次新Chat，第十九轮真实执行及代理验收完成，额度用尽，人工验收pending。用户已回复“继续下一轮的验收 按推荐方案执行”，确认90题×两模式路线和离线设施实施。承接第十七轮交接，只推进响应格式变量，生产仍为 text/cap1。

## 问题、范围与方案选择

第十七轮证明了参数传输、严格解析与并发隔离，但将相同历史原文回放到 json_object 请求，不能证明真实供应商遵守约束或降低故障率。第十六轮的三次缺引号错误与 deepseek-chat 请求/deepseek-flash 响应身份差异仍是历史诊断依据。本轮目标是独立固定输入的 text/json_object 对照；不是 cap2 推广、知识发布、支付口径变更或当前双服务/浏览器验收。

| 方案 | 真实回答额度 | 收益与限制 | 选择 |
| --- | ---: | --- | --- |
| 两组重新回答相同 90 题 | 最多 180 次 | 配对输入完全一致，减少复用旧答案的时间差；仍只有每题每组一次，不能确认稳定故障率 | 推荐 |
| 先做 6 题配对探测 | 最多 12 次 | 可发现不支持参数或明显故障，题量不足以比较低频 JSON 故障和全部答案质量；后续完整实验须另定额度 | 备选 |
| 只生成 json_object，复用旧 cap1 文本答案 | 最多 90 次 | 成本较低，但供应商时间、模型身份与执行环境差异妨碍归因 | 不推荐 |

推荐方案中的每次失败、取消或 SDK 提前报错也消耗额度，不能补跑。没有额外付费兼容性探测；首批正式配对请求本身就是兼容性观察。零故障不能解释为绝无故障；90 题是反复使用的开发回归集，不称独立未见集。

## 已完成的输入预验收

原始输入与来源只读保存于 `tmp/rag-json-answer-20261119/input-investigation.json`。复用既有 `load_frozen_inputs` 校验历史规范语料、题序、文档映射、候选身份与 BGE 排序，再执行 `prepare_contexts` 的正式选择路径；180 次为两个旧配额分支的 Fake 准备调用，没有供应商、Embedding 或 BGE 推理。

只抽取 cap1 的 90 组作为两新组的共同输入；90/90 最终上下文与第十六轮 cap1 逐字相同。以正式 `CustomerServiceContext` 调用当前 prompt 函数重建 system/human 消息，90/90 完整消息也与已落盘 cap1 请求一致。原消息 human 在实际 HTTP 序列化时对应 user，不改变正文或顺序。

- 第十六轮真实报告字节 SHA256：`c34af6aa6b5f96f654e3255ccd239d8da5474ace2db3fc3337b270dcb3dbb704`。
- 90 题的 ID、完整问题、cap1 上下文及完整消息规范化 SHA256：`bf559c24a88cebf92e16f50f67fc1f71b47ed77670455f1444f6cb5710d30b1f`。
- 原标注、独立支付澄清、当前关键代码和依赖锁摘要已收集；不覆盖旧 rubric、报告、批准文件、耗尽账本或任何旧 pin。

实际本地模型为 deepseek-chat、温度 0.1、输出 8192、协议预留 4096、上下文窗口 200000。这是本地冻结配置，不是公共默认 65536；不为本实验扩容。Top8、开启重排顺序、单文档 cap1、最终最多三块、1200/3000 字符及 16384 JSON 字节预算保持。每组的实际完整 messages、模型、温度、输出预算相同，HTTP 仅 json_object 组增加固定 `response_format`，text 组不发送该字段。

## 已实现设施的职责与边界

新 study 为 `rag-json-answer-20261119-v1`，新报告 schema 为 `rag-json-answer-experiment/v1`，两组严格为 `text/json_object`。原 rerank/cap/snapshot/refusal runner 保持 text 保护，不扩展旧 `_runtime_model` 的行为，不把 cap 两组改名伪装成格式实验。

已新增 `rag_json_answer_contracts.py`、`rag_json_answer_budget.py`、`rag_json_answer_experiment.py`、`rag_json_answer_transport.py` 和相应专项测试。纯输入校验及正式 `answer_once` 复用既有实现；新 runtime 直接构造唯一零重试 SDK 客户端，注入两个分别持有固定模式 Settings 副本的适配器，两者共享同一客户端，不调用基类构造器再覆盖或额外建立 SDK 客户端。适配显式 `_invoke(..., response_format=None)` 参数，并保留原 `answer_customer_service` 的严格业务解析。真实 `.env` 不改，客户端或 Settings 不临时切换全局模式。

新准备入口默认离线，`--prepare` 只生成正式 plan 与 `decision=pending` 请求。plan 绑定上述输入、完整两组消息、配对题序、配置、供应商地址指纹、相关代码/uv.lock、当前 SDK 版本、原标注与支付澄清、唯一输出位置及整数 180。当前预验收摘要不是正式 plan 或批准文件；新设施实施后必须重新生成并核对正式绑定，不把本设计中的当前代码摘要当成未来执行摘要。

新 `--live --plan ... --approval ...` 拒绝未批准或任意相关漂移，验证顺序为批准/计划 → 输入/配置/代码/消息 → 新输出和固定账本 → SDK。批准文件严格字段、整数额度及 study/plan/output 全匹配，软件结构校验不能认证批准人，Agent 仍须有可信用户对正式计划和新额度的明确授权。

正式账本固定于 `tmp/rag-json-answer-20261119/json-answer-authorization.jsonl`，不接受任意 `--ledger` 参数。独占创建，发送前 flush/fsync 预占该题该组，重复 key、同授权重启、换输出目录或写入不确定均失败关闭。失败和取消不返还额度；进程崩溃后不自动续跑、不删除锚点重建，本轮没有恢复额度或严格计费 exactly-once 保证。

两组各 90 占位先持久化，单并发按题交替 text→json_object/json_object→text，最大 180 次新回答。模型前输入/预算/账本失败立即停止；任意 HTTP 400 在记录后停止，不继续试 text 或静默探测支持；其他调用错误连续三次停止，未运行项保持。SDK 和应用重试均为 0；不改正式共享客户端现有默认重试策略。

预算必须包围实际发送边界，捕获序列化 HTTP body，核对实际模式、完整消息和预算再发送。报告保留预占/发送区分、原文及规范摘要、usage、SDK/请求模型和响应模型、结束原因、耗时与稳定错误码。观测上限1 MiB，摘要与长度按解压实体计算；gzip重建去除失效编码/长度头。JSON沿原SDK parse终态门禁，text仅拒绝length/content_filter；不扩展为任意非stop结束拒绝。SDK parse 的 length/content_filter 可能早于 `AIMessage` 抛错，须在本地 HTTP transport 层留存有界响应观测，不把缺少 `AIMessage` 当成未发送或未计费；响应捕获超限失败关闭、保存长度/摘要，不截断凑出成功 JSON。禁止记录 Authorization、真实 key、请求头、完整供应商 URL 或异常正文。

## 离线设施验收与真实裁决

设施验收已按以下要求执行 Fake/完整 SDK MockTransport：

1. 准备不构造 SDK/存储/Embedding/BGE、不创建正式账本/live；每题两模式的正式上下文与消息逐字相同。
2. 非法批准、布尔/浮点额度、漂移输入/代码/配置/模式/输出、跨 study、已用账本、预占/fsync 失败全部在发送前拒绝。
3. 180 次固定本地 SDK 响应完整经过新执行器、正式客服解析及持久化；每次发送的模式与消息均符合计划，预占数/发送数精确匹配，socket 拒绝外网。
4. 有效回答/拒答、坏 JSON、未知/重复引用、不支持格式 400、length/content_filter、取消和报告写回故障保留错误；无 text 降级、补引号、重试、额外收费探测或失败项剔除。
5. 非法/超限响应观测、模型身份缺失或改变如实记录，不用请求模型名覆盖实际返回身份。
6. 运行定向测试、compileall、完整 pytest、uv lock --check，独立规格/质量审查后提交设施；不复用第十七轮测试成绩。

真实开始前须提交可审阅的正式 plan 和 pending 请求，并单独取得最多 180 次新 Chat 授权；新增 Embedding/BGE/INDEX/生产写入均为 0。本设计确认本身不创建 approved 文件，不消费模型额度。采用原冻结标注，支付新口径单列后置检查，不把未发布知识混进输入或主成绩。

主要结果分开报告：每组正式 `MODEL_PARSE_FAILED`、供应商/SDK 调用错误、answered/refused、已预占/已发送/未运行；全部 90 项保留分母。诊断可在正式判定后区分 JSON 语法与字段/引用协议错误，但不得修补、替换正式解析判定或把错误 fallback 算正确拒答。

答案质量按完整匿名卡审查事实、无依据 claim、引用支持与安全拒答；仅对 shared blind review 注册严格新 study/schema/arms，旧两 study 行为与 schema 保持。公共卡去除模式、usage、配置、时间和内部诊断，私有映射独立保存。格式主要指标不依赖代理语义评分；语义代理判断与用户人工裁决分别标记，未完成人工审阅不声称人工验收通过。

保留题级配对四格和差值，质量分析按新 study 输出 jsonObjectOnly/textOnly/jsonObjectMinusText，不改旧分析键。报告仅说明本次观测；两组均无格式错误也不能证明约束降低故障率，不自动启用生产 JSON、cap2 或支付知识。当前权威快照、Spring/Python HTTP、浏览器交互不在本实验验收范围。

## 当前交付与下一步

设施实现提交`c1116fd`，完整离线run_live完成180次实际SDK/MockTransport发送与持久化预占，各组89接受/1历史解析错误。180匿名卡全部pending；网络尝试和新真实操作均0，原响应重播不证明新格式效果或质量改善。完整pytest 2103通过/25跳过，compileall/lock通过，独立规格和质量审查通过。严格计划字段、取消传播及gzip问题的红绿修正证据保留，详见[设施聚合](../../../doc/knowledge/product/evaluation/results/json-answer-facility-20261119-v1.json)及[逐轮交接](../../../doc/customer-service-rag-rounds-handoff.md)。

实际正式`tmp/rag-json-answer-20261119/prepared/plan.json`规范SHA256为`9ad2784956f2aeebbf0f5141f041e950d0a8c7ba82a92324ceb742783bb17625`。它绑定24份关键执行文件及依赖、当前SDK、完整两组输入和唯一输出。第十八轮末的原pending请求保留；随后用户回复“是”，批准和可信回复单独存档，正式账本已持久化180条预占，全部发送HTTP200，剩余额度0。实际执行为正式run_live、单并发、零重试、没有transport注入；失败不补跑。新增Chat180，Embedding/BGE/INDEX/生产写入均0。

第十九轮text为88正常/2个MODEL_PARSE_FAILED，JSON为90正常/0解析失败；两次均是finish_reason=stop的原JSON语法错误，无调用错误或未运行，正式解析器离线核对178接受/2拒绝。请求deepseek-chat、全部响应自报deepseek-flash，身份限制保留；不能认证底层模型或证明长期格式故障率降低。

三份独立匿名代理完成180卡初审，另对21题42卡成对复核，主代理核对原证据后为text74/90、JSON76/90，公共事实105/130对109/130。初审70/74、复核和主裁决侧车分别保留，原标注不变。配对73都过、1仅text、3仅JSON、13都失败，差+2.22个百分点，固定seed20261118的10000次bootstrap 95%区间[-2.22,+6.67]个百分点，跨零；不是独立未见集或稳定收益证明。人工模板仍pending。两组付款答案均未直接说明当前平台不支持支付，后置澄清检查未满足且不覆盖主成绩，支付候选仍未发布。

完整真实结果及归因见[真实聚合](../../../doc/knowledge/product/evaluation/results/json-answer-20261119-v1.json)、[第十九轮计划](../plans/2026-11-19-customer-service-json-answer-live-acceptance.md)与[逐轮交接](../../../doc/customer-service-rag-rounds-handoff.md)。生产text/cap1、旧标注/支付语料、19份保护材料及用户原修改保持；当前权威快照、双服务/浏览器E2E、人工裁决仍未完成。本轮仅文档和聚合，不重复第十八轮完整测试；最终独立规格/质量审查及保护、聚合核对均通过，限定提交六份文档与结果；收尾新增真实调用0。推荐先处理证据、范围/引用及支付知识候选，再经人工裁决决定后续方案；不得删除账本锚点、换输出或重复真实运行绕过已用授权。
