# 客服回答 JSON 输出约束候选设计

日期：2026-11-18。阶段：第十七轮方案评审，尚未实现或启用。用户要求继续下一轮优化；具体请求模式及兼容处理待本设计确认。新真实调用额度为零，已耗尽的两份历史账本不得重建或复用。

## 问题与证据

第十六轮固定输入的 180 次真实回答中，三次在 `citedChunkIds` 前缺起始双引号，正式解析正确返回 `MODEL_PARSE_FAILED`。这三份响应均 finish_reason=stop；历史报告和只读解析回放为 177 接受/3 拒绝，属于历史诊断依据，不是本轮测试成绩。不能用补引号或重试后成功替代原故障。

当前 `OpenAICompatibleModel.answer_customer_service` 仅通过系统提示要求 JSON，再经 Pydantic 严格字段及引用校验；共享 `ChatOpenAI` 没有供应商响应格式参数。检索、配额、语料和支付口径不属于本项变量。

本轮源码调查确认本地 langchain-openai=0.3.35、openai=2.54.0。每次调用传入的 `response_format` 会进入请求 payload；LangChain 在该参数存在时切换到 OpenAI SDK 的 `chat.completions.with_raw_response.parse` 路径。不能仅断言 Fake `ainvoke` 收到了 kwargs 就声称完整 SDK 链路已验证，需本地 HTTP transport 捕获真实序列化请求并返回固定响应。真实代理仍可能拒绝参数或忽略约束；请求 deepseek-chat、历史响应自报 deepseek-flash 的身份差异继续保留。

## 三种方案及取舍

| 方案 | 收益 | 代价与限制 | 本轮选择 |
|---|---|---|---|
| 每请求 `json_object` | 限定供应商输出为 JSON 对象，改动小，仍可保留正式严格解析 | 不强制业务 schema，不保证内容或引用正确；兼容代理可能拒绝或忽略 | 推荐，默认关闭，只离线验证候选 |
| 严格 `json_schema` | 支持的供应商可进一步约束字段和类型 | 供应商/代理兼容面更窄，本地 SDK 路径与拒答/截断处理更复杂；本轮没有支持证明 | 本轮不接入 |
| 仅强化提示 | 不新增供应商参数，适配面宽 | 仍无传输层输出约束；会引入提示变量，不能证明解决三次格式错误 | 保持原提示作基线，本轮不改 |

本设计不采用字符串修补、自动重试、动态探测后静默降级或全局客户端默认格式。这些会改变故障语义、调用次数或其他生成能力，妨碍归因。

## 目标与非目标

交付默认关闭、仅作用于客服 `answer_customer_service` 的可选请求模式，以及严格的离线/Fake SDK 验证。默认仍完全采用现有请求参数。当前真实 `.env`、生产 cap1、提示、知识、Top8/BGE、来源与公共 API 不变。

本轮不新增真实实验入口、预算、账本、付费模型探测或真实效果数字，不重新生成答案，不启动/重启正式服务。通过离线检查只证明参数传输和边界行为，不能宣称供应商实际遵守约束、JSON 故障率降低、答案质量提升或端到端通过。人工验收仍待办。

## 配置与触发

新增 `AI_SERVICE_RAG_ANSWER_RESPONSE_FORMAT`，配置字段 `rag_answer_response_format`，仅允许 `text` / `json_object`，默认 `text`。非法值在 Settings 加载时失败；不解析用户问题或文档中的同名内容作为模式，不按模型名自动打开。

只有运维明确配置 `json_object` 且实际进入客服回答调用时，才携带 `response_format={"type":"json_object"}`。`text` 模式不发送 `response_format` 字段，而不是显式发送 `{type:text}`，从而保持旧请求不变。配置在进程启动时读取；回退为 text 并重启。本轮不修改真实 `.env` 或启用候选。

## 调用架构与处理流程

1. `answer_customer_service` 按现有函数生成完全相同的系统/用户消息和字节预算。
2. 读取可信配置。text 调用原 `_invoke(messages)`；json_object 调用带显式可选格式参数的 `_invoke`。内部参数只接受该固定模式，不接收任意调用方 kwargs。
3. `_invoke` 保留完整上下文预算检查与禁用自动 LangSmith tracing 的作用域，再将固定 `response_format` 作为本次 `ainvoke` 参数传入。
4. 不修改共享客户端、不使用实例级临时开关或 ContextVar 切模式，不构造第二个 SDK 客户端，避免并发客服/生成请求互相污染。
5. SDK 返回后仍以原 `_CustomerServiceAnswerPayload` 严格校验 answered/answer/citedChunkIds、额外字段、最多三条唯一且属于实际输入的 chunkId、空拒答协议。
6. 非法 JSON、非法字段或引用仍抛 `CustomerServiceModelOutputError`，正式 RAG 继续标识 `MODEL_PARSE_FAILED` 并返回原系统 fallback；供应商/SDK 失败沿现有 `MODEL_CALL_FAILED` 路径处理，不能改成正常知识不足拒答。

HTML、MULTI_FILE、Vue 生成/修复、路由、需求整理、输入审核和 Reviewer 请求不携带此格式参数。特别测试并发客服和生成调用，不能只看顺序执行。

## 兼容、重试与错误边界

供应商 400/不支持格式：保留失败，不自动再发 text 请求，不改问题/提示/模型。软件不能在本轮零真实调用条件下建立真实供应商支持白名单，也不宣称会在发请求前识别不支持的代理。

供应商忽略参数并返回坏 JSON：正式严格解析继续拒绝。JSON 对象但字段类型、额外字段、未知/重复引用、空回答或拒答协议错误仍拒绝。JSON 格式有效不是证据支持或安全正确的证明。

LangChain/SDK 的 parse 路径可能对 length/content_filter 等结束原因提前抛错，需固定响应验证并保持失败关闭；不会从截断内容凑出成功答案。取消沿原 asyncio 路径传播，不因新增格式吞掉取消。

本功能不新增应用层格式重试或自动降级。正式客户端当前已有 SDK 默认重试行为，本轮不扩大改动到全平台重试策略，也不谎称该客户端已零重试；本地 HTTP 验收显式设 SDK 零重试，只证明本次路径没有二次发送。历史真实评测原有零重试机制保持。

## 历史评测入口保护

不能让旧实验继承新开关后暗中改变基线，或在预占/Embedding 后才因旧 `_invoke` override 不接受参数而失败。

固定 text 的 `rag_rerank_experiment`、`rag_cap_contracts` / cap 入口和 `rag_snapshot_experiment` 在资源/输出/预算前拒绝非 text；`rag_refusal_experiment` 的独立手写 prompt/SDK 也显式拒绝非 text，避免环境配置被静默忽略。维持各入口已有稳定错误码，不修改旧 schema、历史报告、授权文件或旧 pin。cap 原 settings_manifest 不追加新字段破坏已冻结结构，而由前置检查明确限定 text。默认 text 行为与历史报告保持兼容。

后续如需真实 JSON 开关对照，应另建绑定模式、完整消息、SDK 行为和实际模型元数据的新计划及独立额度；不在本轮扩展旧 runner、复用耗尽额度或准备无授权的 live 输出。

## 验证设计

- 配置：默认 text、显式两个值、环境加载及非法字符串/布尔/数字拒绝。
- 请求：text payload 无格式字段；json_object 完整 SDK→MockTransport 序列化请求字段正确，消息、模型、温度和输出预算保持；仅客服带格式，其他模型调用不带。
- 并发：同一适配器的客服/生成并行调用，各自参数隔离，客户端对象身份不变。
- 严格解析：有效回答/拒答保留；坏 JSON、错误类型、额外字段、未知/重复/过量引用、非法拒答在两模式均拒绝；不提供格式修补。
- 故障：固定 400 无 text 降级，正常 stop 坏 JSON拒绝，length/content_filter 失败、取消传播；跟踪抑制和模型前预算拒绝保持。
- 旧工具：非 text 在 SDK、输出目录、授权锚点/预占或 Embedding 前拒绝；默认 text 保持现有回归通过。
- 历史证据：只读使用第十六轮全部 180 份原响应，经完整 MockTransport/正式适配器分别回放 text/json_object，保存成功/失败、原文 hash、请求模式与消息摘要；不把这些旧响应当成供应商在新约束下生成的新答案，不覆写旧解析报告。
- 仓库：Python compileall、完整 pytest、uv lock --check；默认环境、旧/新耗尽账本、旧原始报告、评审、人工模板和用户无关修改的摘要核对，限定中文提交。Java/前端接口不变，本轮不为此重跑真实服务或浏览器。

新增代码和涉及范围内的注释使用中文，正式 API/模型适配/配置/README 按影响同步；不重构无关大文件。原始产物留 `tmp/rag-json-output-20261118/`，提交脱敏汇总与逐轮交接，历史成绩只作诊断依据。

## 确认点

待确认的是上述默认 text、只对客服显式 opt-in json_object、旧实验非 text 前置拒绝及零新增真实调用的完整候选边界。确认后再编写实施计划并执行代码；当前文档不代表功能已实现或供应商支持已验证。

## 方案自审与当前交付

本次自审核对了正式回答适配器、四类历史实验的配置入口、SDK 路径和原始交接记录：cap 使用独立配置 checker，不能只保护 rerank；text 继续不带新增关键字，保留旧 `_invoke` 子类兼容。本文没有待补占位或将候选写成现状；参数传输、并发隔离及故障行为均列为确认后的验证要求，尚无本轮运行成绩。

本阶段只提交候选设计和逐轮交接，不修改生产代码、配置或依赖。确认后进入实施计划，真实效果对照仍须另外确定新计划和额度。
