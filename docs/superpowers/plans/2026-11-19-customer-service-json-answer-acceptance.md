# 客服 JSON 回答对照设施实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 交付已确认90题×text/json_object路线的独立设施，完成零真实调用验收，生成正式pending计划；不创建真实approved文件或消费额度。

**Architecture:** 独立冻结契约、账本和执行器复用正式RAG选择及客服解析，两个固定模式适配器共用唯一零重试SDK。HTTP传输边界校验完整载荷、先持久化预占再尝试发送，并在SDK解析前保存有界响应观测。新盲审study显式注册，旧runner/schema及生产配置保持。

**Tech Stack:** Python3.12、Pydantic、LangChain/OpenAI SDK、httpx MockTransport、pytest。

---

用户已回复“继续下一轮的验收 按推荐方案执行”，确认设计及离线设施实施。真实额度仍须设施就绪后按实际正式plan另行明确批准。沿用当前dev检出及本地.venv，不改.env、不启动服务，19份保护摘要在`tmp/rag-json-answer-20261119/initial-state.json`；README原围栏修改不提交。

## 任务一：独立设施、发送边界与盲审注册

**Files:**
- Create: `ai-service/src/ai_service/orchestration/rag_json_answer_contracts.py`
- Create: `ai-service/src/ai_service/orchestration/rag_json_answer_budget.py`
- Create: `ai-service/src/ai_service/orchestration/rag_json_answer_experiment.py`
- Modify: `ai-service/src/ai_service/orchestration/rag_blind_review.py`（仅显式新增study/schema/arms）
- Create: `ai-service/tests/test_rag_json_answer_contracts.py`
- Create: `ai-service/tests/test_rag_json_answer_budget.py`
- Create: `ai-service/tests/test_rag_json_answer_experiment.py`
- Create: `ai-service/tests/test_rag_json_answer_blind_review.py`

- [ ] 先编写红测并留存日志：非法批准/布尔额度/完整消息漂移在SDK、输出和账本前拒绝；真实SDK与MockTransport断言每个HTTP请求预占已落盘。

```python
assert request['decision'] == 'pending'
assert plan['maxChatCalls'] == 180
assert len(plan['schedule']) == 180
assert not AUTHORIZATION.exists()
assert requests[0].get('response_format') is None
assert requests[1]['response_format'] == {'type': 'json_object'}
```

- [ ] 实现contracts：`STUDY_ID='rag-json-answer-20261119-v1'`、`ARMS=('text','json_object')`；`prepare(output)`用正式Fake选择得到共同cap1上下文。绑定第十六轮cap报告字节pin及90题完整消息pin（设计中已给出）、原rubric/文档/检索身份、每题两组完整消息、settings/provider指纹、SDK版本、相关代码/uv.lock、交替schedule、唯一liveOutput及整数180。测试注入合成源可替换pin，正式入口不可接受调用方任意pin。

- [ ] 实现严格`validate_approval(approval, plan)`与`validate_plan(plan, cfg)`：Pydantic严格字段、整数额度180、study/plan/output全匹配；重新准备上下文及消息，代码/配置/SDK/输入漂移、旧授权或live目录存在均在副作用前拒绝。固定text真实环境作为准备基线，运行时仅复制模式；不能对旧cap计划做改名或更换hash。

- [ ] 实现`JsonAnswerAuthorizationBudget`：固定新锚点`tmp/rag-json-answer-20261119/json-answer-authorization.jsonl`独占创建，验证90唯一题交替180键；flush/fsync持久化header/reserved，重复/越界/关闭/写回不确定均失败关闭，失败取消不返还，不新增恢复或任意ledger路径。

- [ ] 实现唯一共享零重试SDK、两固定Settings适配器与实际transport边界。`_invoke(self,messages,*,response_format=None)`先完整预算，显式JSON单次kwargs，不能变共享client/model_kwargs。序列化HTTP body核对实际expected messages/model/temperature/token预算/stream=false/格式后再预占、标记尝试发送并调用inner transport。拒绝失效key或二次传输，max_retries=0、应用重试0。所有注释中文。

- [ ] 有界读取响应（固定上限并入plan/implementation绑定），在SDK parse前保存rawResponse、raw/HTTP摘要、usage/model/finish白名单及HTTP状态。超限保存长度/hash但失败关闭；异常不输出异常正文、头、URL或凭据。捕获400/length/content_filter/取消均保留预占与尝试发送事实；不靠AIMessage存在判断是否发送，不通过截断/补引号形成成功。

- [ ] 执行器先落盘180占位，单并发按题交替两模式、都cap1，运行正式`answer_once`并核对实际上下文。模型前/账本/保存失败立即停止，400立即停止，其他错误连续三次停止；未运行保留每组90分母，取消传播并条件保存/关闭。报告区分MODEL_PARSE_FAILED/调用失败、reserved/attempted/not_run、请求/响应模型、usage/耗时，真实操作计数仅Chat。

- [ ] shared blind review新增严格study`rag-json-answer-20261119-v1`、schema`rag-json-answer-experiment/v1`、两组text/json_object、分析schema`rag-json-answer-analysis/v1`和差值键`textOnly/jsonObjectOnly/jsonObjectMinusText`。严格防跨study报告/包/映射/评审，公共包去掉模式及内部字段，pending不能生成质量收益；旧study测试保持。格式指标独立统计正式错误，不把fallback计正常拒答。

- [ ] 执行定向测试及保存红绿日志；独立规格审查→修复→独立质量审查→必要复审，限定中文提交。

```powershell
uv run --offline pytest tests/test_rag_json_answer_contracts.py tests/test_rag_json_answer_budget.py tests/test_rag_json_answer_experiment.py tests/test_rag_json_answer_blind_review.py tests/test_rag_cap_experiment.py tests/test_rag_cap_blind_review.py tests/test_rag_response_format_guards.py -q --junitxml=../tmp/rag-json-answer-20261119/facility-target.xml
git diff --check
git log -1 --pretty=%s
```

## 任务二：真实历史输入的完整离线运行、正式pending计划与交接

**Files:**
- Modify: `ai-service/README.md`（仅本轮追加）
- Modify: `docs/superpowers/specs/2026-11-19-customer-service-json-answer-acceptance-design.md`
- Modify: `docs/superpowers/plans/2026-11-19-customer-service-json-answer-acceptance.md`
- Modify: `doc/customer-service-rag-rounds-handoff.md`（第十八轮）
- Modify: `doc/knowledge/product/README.md`
- Create: `doc/knowledge/product/evaluation/results/json-answer-facility-20261119-v1.json`
- Evidence only: `tmp/rag-json-answer-20261119/`

- [ ] 本地新脚本用真实历史冻结输入、真正SDK+MockTransport完成整个新`run_live`路径180次Fake发送，测试专用approved/锚点仅在独立临时目录注入，固定正式锚点保持不存在。每个request核对预占、完整HTTP消息和mode，所有输出上下文与cap1逐字一致；Windows先创建事件循环后禁止socket网络。

- [ ] 通过新prepare入口生成`tmp/rag-json-answer-20261119/prepared/plan.json`和`authorization-request.json`，decision严格pending，不创建live、正式账本或approved。实际正式plan绑定当前SDK和最终执行文件摘要；后续实现审查若改代码则在创建正式计划前修正，不用无关文档HEAD造成无效。

```powershell
uv run --offline python -m ai_service.orchestration.rag_json_answer_experiment --prepare --output ../tmp/rag-json-answer-20261119/prepared
uv run --offline python -m compileall -q src
uv run --offline pytest --junitxml=../tmp/rag-json-answer-20261119/python-full.xml
uv lock --check --offline
```

- [ ] 保存离线完整report/匿名180卡/pending导入和sha，不把Fake回答算真实质量。聚合真实额度仍0、正式ledger/live不存在、输入pin、实际planhash、测试精确计数、已知警告/失败修正及未测项。

- [ ] 同步README/产品/设计/逐轮交接：已实现的命令、只改格式路线、SDK零重试仅实验、固定账本及失败取消、响应捕获上限、模式/身份/质量诊断、真实另批180、生产text/cap1与人工/E2E待办。README原字节保护只允许追加，精确暂存新hunk，其他18保护文件字节全等。

- [ ] 最终独立规格/质量审查、保护摘要和两仓状态核对，更新checkbox后限定中文文档提交。交付准确哈希、完整测试计数、正式pending计划hash及180额度请求；不得创建批准文件、发送真实请求或重建旧账本。
