# cap1/cap2 固定输入答案实验实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 subagent-driven-development，单实现者、先规格后质量审查。已获“继续下一轮”的设施准备授权；真实调用具体额度尚未批准，不因计划或代码产生授权。

**Goal:** 交付默认离线、明确批准后最多 180 次回答的新实验入口与双 study 盲审支持。

**Architecture:** 固定历史输入 → 正式选择/预算 Fake 预备 → 绑定计划与 pending 请求 → 独立持久预占 → 正式回答 → 同 study 盲包。生产、旧账本和旧评测入口不改。

**Tech Stack:** Python、Pydantic、pytest、现有 LangChain 客户端及正式 RAG。

设计依据：`docs/superpowers/specs/2026-11-18-rag-cap-answer-experiment-design.md`。沿用当前 dev，旧 README 围栏和取消计划、前端三项修改保留；不启动正式服务。

## 任务 1：冻结契约、预算与新入口

**Create:** `ai-service/src/ai_service/orchestration/rag_cap_contracts.py`、`rag_cap_budget.py`、`rag_cap_experiment.py`；`ai-service/tests/test_rag_cap_contracts.py`、`test_rag_cap_budget.py`、`test_rag_cap_experiment.py`。必要时仅给 `rag_rerank_experiment._finish_run` 增加兼容默认错误码参数；README 由主 Agent 独立写。

- [x] 先加行为测试，保留红测 XML/log。最小用例：prepare 不创建 SDK/固定锚点；live 使用 pending 文件在资源前抛稳定错误；重复 key 和 fsync 不确定后不得调用发送替身；配置/源排序/context 损坏拒绝。
- [x] 实现 `load_frozen_inputs()`、`prepare(output)`、`validate_plan(plan, settings)` 的严格核对。固定来源报告/rubric/行/映射/向量/候选与前轮 contexts；当前 settings 的模型、提示、预算等与原报告一致。实现文件与输入 hash 漂移在发送前失败，不泄露凭据。
- [x] 实现独立 `CapAuthorizationBudget`，固定 study/锚点、180 个唯一计划 key，`reserve_chat(key)` 在发送前持久预占；没有 embedding 接口。测试独占/换输出拒绝、未知/重复/第181次、flush/fsync 中毒与子进程退出留账。
- [x] 实现默认离线/显式 prepare/live CLI。prepare 输出 plan 和 pending request，不创建锚点；live 先严格核对 approved/计划/180/输出/实现与配置，再建账本和唯一零重试模型。绑定最终完整 contexts 的包装层先检查再调用底层模型。
- [x] 固定交替 cap1/cap2、单并发、180占位；每条即时保存。三连供应商错误停止；取消原样传播、失败和 not_run 留分母。报告和清理解耦，测试 save、客户端和账本关闭异常。
- [x] 定向 `uv run pytest tests/test_rag_cap_contracts.py tests/test_rag_cap_budget.py tests/test_rag_cap_experiment.py tests/test_rag_rerank_experiment.py -q --junitxml=../tmp/rag-cap-answer-20261118/tool-validation/runner.xml`，独立审查、自检、限定中文提交。

## 任务 2：双 study 盲审

**Modify:** `ai-service/src/ai_service/orchestration/rag_blind_review.py`；**Create:** `ai-service/tests/test_rag_cap_blind_review.py`。保留 `tests/test_rag_blind_review.py` 的全部旧断言。

- [x] 先红测：新 study 180 卡正常导出/导入，旧/new study 及 arms 交叉拒绝；pending 无改善值，Fake 完整代理结果正确命名 cap2MinusCap1。
- [x] 固定白名单注册 old→rerank_off/rerank_on、new→cap1/cap2，schema study 字面量兼容两个。按 study 绑定公共/私有包和评审，不放宽未知值或旧拒绝条件。
- [x] 旧输出键不变，新分析用 cap1Only/cap2Only/cap2MinusCap1。公共卡不含 cap1/cap2、配额、分数、usage、耗时、私有映射或调用次序；全180保留故障/未运行。
- [x] 定向 `uv run pytest tests/test_rag_blind_review.py tests/test_rag_cap_blind_review.py -q --junitxml=../tmp/rag-cap-answer-20261118/tool-validation/blind.xml`，规格/质量审查后限定中文提交。

## 任务 3：主 Agent 离线验收、交付及具体预算请求

- [x] 保存本轮初始保护文件摘要；在本机新 tmp 路径运行 prepare，核对 90题×2、原输入/计划 fingerprint，pending/live 在 SDK/账本前拒绝。
- [x] 网络禁止阶段用 Fake SDK/模型走完整新运行器及发送前预占（测试用隔离锚点，不能创建正式授权），保存180结果、完整匿名包和pending人工模板，严格导入；单列 Fake 调用，真实调用为0。
- [x] `uv run python -m compileall -q src`、`uv run pytest --junitxml=../tmp/rag-cap-answer-20261118/tool-validation/python-full.xml`、`uv lock --check`；必要定向复验保留新旧日志。
- [x] 更新 ai-service README（隔离用户原 hunk）、产品 README、第十五轮交接及脱敏设施结果；记载失败、未测项和零真实调用。独立最终审查、保护摘要核对、中文提交。
- [x] 代码/文档提交后生成最终 plan 与 pending 批准文件，使用户能审阅调用数、来源、模型参数、输出位置和失败计费规则。只此时请求“最多180次新回答、0 Embedding/BGE/INDEX”，未获明确答复不得创建正式锚点或 live 发送。

后续第十六轮记录：第十五轮交付提交 `346c26e` 后，最终计划指纹为 `fb3f2ac0ab7b4d02df9185a2f26144b37b1b8e9743801e1498fe1f19653a4544`；用户明确答复“批准本轮最多180次真实回答”，才建立独立批准收据及正式账本。真实执行与代理初审另记于 `doc/customer-service-rag-rounds-handoff.md` 第十六轮，不回写第十五轮当时的零真实调用或待批准状态。人工验收继续待办。
