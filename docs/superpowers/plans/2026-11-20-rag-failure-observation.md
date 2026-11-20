# 客服失败原因观测 Implementation Plan

> **For agentic workers:** Use subagent-driven-development for the isolated diagnostic fix and inline integration for the offline report, followed by specification and quality review.

**Goal:** 修正观测归因，提供不调用供应商的失败统计、证据存活与人工评测脚本，保留原业务策略。

**Architecture:** 复用 `customer_service_trace`、固定研究报告和 `rag_blind_review`。运行时仅修正固定诊断字段；离线工具严格绑定报告及标注，缺阶段保留 UNKNOWN。详细输出只落新建 tmp 目录。

**Tech Stack:** Python 3.12、pytest、Pydantic、现有 LangGraph/FastAPI 工程。

设计依据：`doc/customer-service-failure-observation-handoff.md`，用户于 2026-11-20 确认 B 方案。当前 dev 检出复用本地依赖与历史材料，保留 README 原修改和未跟踪取消计划；不新增真实调用或改配置。

## Task 1：运行时诊断

- [x] 修改 `ai-service/src/ai_service/orchestration/customer_service_rag.py` 和 `customer_service_trace.py`，新增 `ai-service/tests/test_rag_failure_trace.py`。
- [x] 先故障注入复现空权威库、作用域失败、模型前后复核失败及 Embedding/检索误归因。
- [x] 固定原因白名单补齐；阶段固定标记区分读取/模型前/模型后，保留已有已知故障。示例验收：`assert trace.snapshot()['terminal']['reasonCode'] == 'KNOWLEDGE_PUBLICATION_EMPTY'`，`assert len(model.calls) == 0`。
- [x] `uv run --offline pytest tests/test_rag_failure_trace.py tests/test_customer_service_trace.py tests/test_authoritative_knowledge.py` 红绿对照；取消、日志失败、未知异常与敏感内容边界纳入测试。

## Task 2：离线观测与人工入口

- [x] 新建 `ai-service/src/ai_service/orchestration/rag_failure_observation.py`、必要的证据分析辅助模块及 `ai-service/tests/test_rag_failure_observation.py`。
- [x] 入口接受 `--report`、`--report-sha256`、`--output`，可选既有匿名包和人工评审；通过 `load_rubric()`、`validate_results()`、`summarize()` 复用固定研究契约。
- [x] 固定各组 90 分母，分别输出 answered/refused/error/not_run、固定失败码；不得把未知异常正文复制到报告。
- [x] 阶段可用性和身份校验先于分析；只有完整、有绑定的原文才允许判断支持片段的存活。`assert missing_stage['state'] == 'UNKNOWN'`；仅 final context 不能推断召回失败。
- [x] 导出既有匿名模板供实际人工填写，导入复用严格校验；pending/agent 都保持 `humanOverallQualityImprovement is None`。输出必须是新目录，既有输入字节不变。
- [x] 用 Fake 报告覆盖重复/缺样本、hash/schema 错误、阶段损坏/裁剪、pending/agent/人工导入、覆盖拒绝；执行 `uv run --offline pytest tests/test_rag_failure_observation.py`。

## Task 3：历史只读回放和交付

- [x] 对第十九轮原报告及原盲包做 hash 绑定回放，观察现有阶段材料是否充分；不重跑模型，不更改旧材料。
- [x] 完成规格和代码质量审查；执行 `uv run --offline python -m compileall -q src`、`uv run --offline pytest`、`uv lock --check --offline`，实际结果写交接。
- [x] 同步 `ai-service/README.md`、新交接和原逐轮交接，提交脱敏统计；以 index 定向暂存 README 追加，保留用户原围栏修改。
- [x] `git diff --check`、`git diff --cached --check`、`git log -1 --pretty=%s` 后仅提交本轮文件，中文 Conventional Commits。汇报提交、命令结果、未测在线范围和下一轮建议。

验证及边界以本轮交接和脱敏聚合为准；规格与质量审查通过，人工仍 pending。
