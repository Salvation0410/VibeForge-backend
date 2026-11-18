# 客服单文档块配额实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development；单个实现者按任务执行，随后独立规格审查和质量审查。用户已授权上轮明确建议的可回退 cap2 路线，不重复询问执行方式。

**Goal:** 增加默认 1、可选 2 的单文档块配额，固定三块总预算，完成三端来源契约与正式路径回放验证。

**Architecture:** 只改变 Python 过滤/重排后的文档配额，不变 wire。Java 通过本地 HTTP 替身和真实 Client/Service 验证来源，前端新增独立测试文件。默认保持 cap1，不修改真实配置或沿用耗尽的模型额度。

**Tech Stack:** Python/Pydantic/FastAPI/pytest、Java 21/JUnit/本地 HttpServer、TypeScript/node:test。

设计依据：`docs/superpowers/specs/2026-11-18-rag-document-chunk-limit-design.md`。当前后端 `dev`，前端为独立仓库；原 README 围栏及取消计划、前端三项既有修改的字节摘要在 `tmp/rag-cap-rollout-20261118/initial-state.json`。

## 任务 1：Python 配额与基线保护

**Files:** `ai-service/src/ai_service/config.py`、`orchestration/customer_service_rag.py`、`orchestration/customer_service_trace.py`、`orchestration/rag_rerank_experiment.py`、`orchestration/rag_snapshot_experiment.py`；`.env.example`；相关 `tests/test_customer_service_rag.py`、`test_customer_service_trace.py`、`test_gateway_and_config.py`、`test_rag_rerank_experiment.py`/`test_rag_snapshot_experiment.py`（若无对应文件则在既有评测测试中补入口保护）。README 由主 Agent 单独修改并隔离暂存。

- [x] 写行为测试并运行，确认 cap2/环境配置/诊断新要求在实现前失败，保留红测日志。
- [x] 添加 `rag_document_chunk_limit: int = Field(default=1, ge=1, le=2)`；正常环境变量 `1/2` 可读取，拒绝布尔/非整数/范围外输入，不把 True 隐式当 1。
- [x] Service 初始化保存配置，调用 `_select_documents(ranked, limit, *, per_document_limit=1)`，按当前稳定顺序保留同文档至多两块，最终三块上限不变。保持 cap1 默认调用兼容；配额计数、同块去重不混淆，主要诊断原因按设计输出。
- [x] trace 固定配置字段增加 `documentChunkLimit`，原正文隐私边界不变；`.env.example` 明示默认 1 及设置 2 后重启、回退 1。
- [x] 两个固定 cap1 真实评测入口在创建供应商客户端/授权之前拒绝 cap2，并补不发请求的测试；拒答历史上下文回放不改。
- [x] 定向运行 `uv run pytest tests/test_customer_service_rag.py tests/test_customer_service_trace.py tests/test_gateway_and_config.py tests/test_rag_rerank_experiment.py -q --junitxml=../tmp/rag-cap-rollout-20261118/python-target.xml`，输出至同目录日志。自检差异后交独立审查，提交范围仅以上任务文件。

## 任务 2：Java 多块来源真实协议解析测试

**Create:** `src/test/java/com/yupi/yuaicodemother/customerservice/CustomerServiceMultiChunkSourcesTest.java`，不修改生产 Java。

- [x] 使用本地 `HttpServer` 回送请求实际 requestId，真实 `CustomerServiceAiClient` 与 `CustomerServiceAnswerServiceImpl`，Mock Mapper 返回活动文档。Fixture 必须 finally/close 停止 HTTP 服务，不调用真实 Python。
- [x] 验证 `7:2:1,7:2:0,8:3:0` 三来源及原顺序/locator/excerpt全部保留；明确同文档第二块不被按文档去重。
- [x] 验证重复 chunk、第二块旧版本/身份错误/索引越界/文档非活动状态使整次失败，超过三项在 HTTP 客户端解析边界拒绝，不返回部分来源。
- [x] 运行 `mvn "-Dtest=CustomerServiceMultiChunkSourcesTest,CustomerServiceAnswerServiceTest,CustomerServiceAiClientTest" test`，保存 `tmp/rag-cap-rollout-20261118/java-target.log` 和本次 XML 汇总；独立审查后提交本文件。

## 任务 3：前端纯函数多块来源回归

**Create in frontend repo:** `tests/customerServiceSources.test.ts`；不修改现有 `tests/customerService.test.ts` 或用户三项修改。

- [x] 构造两个相同 documentId、不同 chunkId 的来源和第三文档来源，验证归一化保留三者及返回顺序；相同 chunkId 重复仍去重，第四块不超总上限。
- [x] 验证通过 `normalizeCustomerServiceState` 的真实成功归一化路径保留三来源、摘要仍有界，不生成私有下载链接或合并块正文。
- [x] 在前端运行 `node --test --experimental-strip-types tests/customerServiceSources.test.ts tests/customerService.test.ts`，保存本轮输出；记录 `normalizeCustomerServiceSources` 当前函数与已提交 HEAD 相同，区分其他既有工作树变化。
- [x] 独立审查后只提交新测试文件，保持三个旧修改未暂存。运行 type-check/build-only 及根 AGENTS 指定的相关纯函数测试作为本轮交付验证。

## 任务 4：正式路径回放、完整验证与交付

- [x] 主 Agent 使用临时 `formal-replay.py`，对第十二轮实际候选/BGE 顺序及第十三轮 `replay.json` 各 90 题/配额执行正式 `CustomerServiceRagService`，Fake Embedding/Store/Reranker/Answer Model。断言 cap1/cap2 均逐字匹配既有最终上下文，逆序引用输出身份仍准确。记录 180 个 Fake Model 调用与 0 供应商调用，不能当作真实问答成绩。
- [x] Python 执行 `uv run python -m compileall -q src`、`uv run pytest --junitxml=../tmp/rag-cap-rollout-20261118/python-full.xml`、`uv lock --check`；Java 执行干净编译及任务 2 相关测试；前端执行上述测试、type-check/build-only，保留失败及复验日志。
- [x] 同步 `ai-service/README.md` 配置表和选择流程；用隔离 hunk 暂存保留用户旧代码围栏。追加第十四轮真实范围、方案/触发/模块、验证、部署状态和下一轮；产品说明明确开关不等于 cap2 质量已通过。
- [x] 独立最终证据审查；核对 `.env`、原授权账本、原答案/语料和用户既有修改未变；`git diff --check`、中文 Conventional Commit，仅提交本次任务范围，报告两仓提交。
