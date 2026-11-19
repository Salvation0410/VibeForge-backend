# 客服 JSON 输出约束实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 实现已确认的客服单次请求 json_object 开关，默认 text，完成零真实调用的隔离、严格解析与旧实验保护验收。

**Architecture:** 配置只在客服回答入口决定是否向 `_invoke` 传入显式可选参数。共享 ChatOpenAI 不变，严格业务解析不变；四类旧实验在任何资源、输出与预占之前拒绝非 text。

**Tech Stack:** Python 3.12、Pydantic Settings、LangChain OpenAI 0.3.35、OpenAI SDK 2.54.0、httpx.MockTransport、pytest。

---

用户已明确回复“确认，按推荐方案实施”，对应设计提交 `c16d259`。本轮沿用当前 dev 检出及本地 `.venv`，保持冻结证据路径；不改真实 `.env`，不启动服务，不新增真实调用。已有 16 份保护摘要位于 `tmp/rag-json-output-20261118/initial-state.json`。README 原用户围栏修改不能提交。

### 任务一：客服请求配置与 SDK 边界

**Files:**
- Modify: `ai-service/src/ai_service/config.py`
- Modify: `ai-service/src/ai_service/models/openai_compatible.py`
- Create: `ai-service/tests/test_customer_service_response_format.py`

- [x] 编写失败测试并运行，确认旧代码未发送 json_object。测试通过真正 ChatOpenAI/OpenAI SDK、本地 MockTransport 捕获序列化 body；固定虚假密钥、`.invalid` 地址、SDK 零重试，禁止外网。

```python
assert body['response_format'] == {'type': 'json_object'}
assert body['model'] == 'test-model'
assert body['temperature'] == 0.1
```

- [x] 新增配置并保持 Settings 的环境加载及非法值拒绝：

```python
rag_answer_response_format: Literal['text', 'json_object'] = 'text'
```

- [x] 修改 `_invoke` 接口，仅接受固定模式；text 不给旧 client/subclass 注入关键字，不修改客户端实例：

```python
async def _invoke(self, messages, *, response_format: Literal['json_object'] | None = None):
    self._check_budget(messages)
    with tracing_context(enabled=False):
        if response_format == 'json_object':
            return await self._client.ainvoke(messages, response_format={'type': 'json_object'})
        return await self._client.ainvoke(messages)
```

客服先构建现有 messages，再按 `getattr(getattr(self, '_settings', None), 'rag_answer_response_format', 'text')` 判断。json_object 显式传参，text 保留 `_invoke(messages)`；后续 Pydantic/引用/拒答解析逐字保留。涉及注释改为中文。

- [x] 测试默认/环境/非法配置，双模式完整请求消息一致和严格字段/引用/拒答负例；400 不降级，SDK parse 的 length/content_filter 失败关闭，取消传播，预算在发送前拒绝、tracing 关闭。同一适配器并发客服/生成、共享 client 身份不变；路由/生成/修复/Reviewer/审核/需求整理均不带该字段。
- [x] 运行定向检查并保存红绿记录：`uv run pytest tests/test_customer_service_response_format.py tests/test_openai_compatible.py tests/test_gateway_and_config.py -q`。
- [x] 完成独立规格审查、代码质量审查，修复后复审；提交上述任务文件，中文提交信息 `feat(rag): 增加客服单次请求JSON输出约束`。

### 任务二：冻结 text 实验前置拒绝

**Files:**
- Modify: `ai-service/src/ai_service/orchestration/rag_rerank_experiment.py`
- Modify: `ai-service/src/ai_service/orchestration/rag_cap_contracts.py`
- Modify: `ai-service/src/ai_service/orchestration/rag_snapshot_experiment.py`
- Modify: `ai-service/src/ai_service/orchestration/rag_refusal_experiment.py`
- Create: `ai-service/tests/test_rag_response_format_guards.py`

- [x] 编写失败测试，四入口传 json_object；用禁止构造的资源/SDK、禁止预占的账本替身，以及不存在的输出目录验证拒绝时机。
- [x] rerank 原 `check_settings` 增加 `cfg.rag_answer_response_format != 'text'`，保留 `UNSUPPORTED_BASELINE_SETTINGS`。
- [x] cap 独立 `check_settings` 增加同条件，保留 `SETTINGS_CHANGED` 和原 `settings_manifest`；不得改冻结 schema/pin。
- [x] snapshot 已有基线检查增加同条件，保留 `RAG_EVAL_UNSUPPORTED_BASELINE_CONFIG`；refusal 在 Settings 后、ChatOpenAI 构造前用相同错误拒绝非 text：

```python
if cfg.rag_answer_response_format != 'text':
    raise ValueError('RAG_EVAL_UNSUPPORTED_BASELINE_CONFIG')
```

- [x] text 回归保持；非 text 的 run_live/配置路径不得创建输出、预占或触发 Embedding/BGE/SDK。执行 `uv run pytest tests/test_rag_response_format_guards.py tests/test_rag_rerank_experiment.py tests/test_rag_cap_contracts.py tests/test_rag_cap_experiment.py tests/test_rag_refusal_experiment.py -q`；实际存在的测试文件以 rg 清单为准。
- [x] 规格及质量审查后限定提交，中文信息 `fix(rag): 固定旧评测入口的文本模式基线`。

### 任务三：只读回放、全套验证与文档

**Files:**
- Modify: `ai-service/.env.example`
- Modify: `ai-service/README.md`（仅本任务新增内容）
- Modify: `docs/superpowers/specs/2026-11-18-customer-service-json-output-design.md`（记录确认及实施状态）
- Modify: `doc/customer-service-rag-rounds-handoff.md`
- Modify: `doc/knowledge/product/README.md`
- Create: `doc/knowledge/product/evaluation/results/json-output-offline-20261118-v1.json`
- Evidence only: `tmp/rag-json-output-20261118/`，不提交。

- [x] 本地新增独立回放脚本，只读历史 cap `prepared/live/report.json`，每份原文分别经过 text/json_object 的完整 SDK MockTransport 和正式适配器。保存 360 项判定、原文 SHA256、实际请求模式与消息 hash；语义成绩不重算，旧报告/账本不写。预期两模式各 177 接受/3 拒绝；分母为历史原文，不能称新生成。
- [x] 运行并保存 `uv run python -m compileall -q src`、`uv run pytest --junitxml=../tmp/rag-json-output-20261118/python-full.xml`、`uv lock --check`；完成后不无故重复全套。
- [x] 样例新增 `AI_SERVICE_RAG_ANSWER_RESPONSE_FORMAT=text` 和中文注释。README/产品文档说明默认值、仅客服参数、重启配置、严格解析、供应商不兼容失败与 SDK 默认重试仍存在。旧真实模式非 text 提前拒绝，未来 JSON 真实对照须新计划及新额度。
- [x] 交接记录触发场景、方案选择、调用流程、错误/取消/并发边界、新测试与历史回放分开、零新增真实调用、人工待办与下一轮建议。
- [x] 核对保护摘要；README 仅允许新说明，原围栏 hunk 保留。使用临时 Git index 或精确 patch 暂存 README 新 hunk，其他15份保护文件要求字节相同。后端/前端 status 与 `git diff --check`、暂存列表核对后限定中文文档提交。
- [x] 最终独立规格/质量审查，报告准确提交哈希、完整测试计数与依赖警告。生产默认 text/cap1；真实效果与人工裁决未验收。

## 2026-11-19 收尾记录

任务一、二分别提交 77a26aa、da89709；任务三完成历史原文 360 项本地 SDK 回放，text/json_object 各 177 接受/3 拒绝。完整 Python 2011 通过、25 跳过、0 失败/错误，compileall 与离线依赖锁检查退出 0，保留两条依赖警告。最终独立规格与质量审查均通过，16 份保护文件核对不变（README 仅追加任务说明），零新增真实调用，候选未启用。命令记录和审查证据位于 tmp/rag-json-output-20261118/；脱敏结果与第十七轮交接已同步。本文随任务三限定文档提交交付，准确最终哈希由交付回复报告。
