# 客服知识内容与 90 条评测集实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 executing-plans 在当前会话逐项执行。以下步骤使用复选框记录完成情况。

**Goal:** 扩充平台使用文档和可导入客服知识，提供固定 90 条真实产品问题及可核验的标注。

**Architecture:** 保留现有单库、ETL、问答和评测协议。知识文档按功能逻辑分类，规划附录不参与导入；通过文档注册表将逻辑 ID 映射为管理员上传后得到的真实 ID。前端只扩充 DocsView 说明内容，不修改流式生成行为。

**Tech Stack:** Markdown、JSON、Python 3.12、现有 RAG 评测模块、Vue 3 / TypeScript。

---

### Task 1：盘点事实与内容编写

**Files:** 新增 `doc/knowledge/product/README.md`、`manifest.json`、`documents/01-platform-account.md` 至 `documents/11-faq.md`；新增不导入的 `planning/product-specification.md`。

- [ ] 阅读前端 DocsView、页面路由、生成与社区页面，以及 Spring App/Community 服务和 Python ETL。
- [ ] 编写 11 篇知识文档，每个步骤注明当前功能或推荐方式，包含异常和 FAQ。
- [ ] 注册表保留逻辑 ID、分类、正文路径、状态、受众、事实来源；规划附录不出现在导入注册表中。
- [ ] README 写清上传、替换旧文档、索引等待、真实 ID 映射和回滚步骤。管理员使用说明是公开流程介绍，不含内部密钥。

### Task 2：固定 90 条评测与标注

**Files:** 新增 `doc/knowledge/product/evaluation/dataset.json`、`annotations.json`、`document-id-map.example.json`。

- [ ] 编写恰好 90 条问题；分类配额为 8/24/10/14/14/5/15。
- [ ] 每条标注问题类型、期望事实、来源小节、禁止结论和审核状态；缺少人工审核时不能填“已人工审核”。
- [ ] 复用 customer-service-rag-eval/v1，不替换原 6 条单元测试 fixture。原 fixture 只测试评测计算，不作为产品基线。
- [ ] ID 映射示例保持空映射，运行真实评测前必须填入上传后的实际 ID 和版本，禁止虚构服务端 ID。

### Task 3：可执行校验与真实评测入口

**Files:** 新增 `ai-service/src/ai_service/orchestration/product_knowledge_evaluation.py`、`ai-service/tests/test_product_knowledge_evaluation.py`；更新 `ai-service/README.md`。

- [ ] 校验 90 条数量、配额、唯一问题、全部标注、文档引用和逐小节事实锚点。
- [ ] 默认命令只执行校验和本地 ETL 解析，不调用模型、不写 Milvus；记录解析和分块规模。
- [ ] 真实评测读取 ID/版本映射，启动现有 FastAPI lifespan 依赖；记录检索与重排分别排序，版本不一致即中止；回答沿用现有 RAG 服务。
- [ ] 每条真实评测设置超时，错误计数而不冒充正确拒答；报告说明额外检索诊断调用的延迟开销；输出到调用者指定、Git 忽略的目录。
- [ ] 测试错误映射、错误配额和无答案标注，不用 Fake Model 结果声称真实召回质量。

### Task 4：扩充用户可见说明

**Files:** 修改独立前端仓库 `src/pages/DocsView.vue`。

- [ ] 扩充账号、类型选择、停止与预览、部署下载、社区步骤、客服与 FAQ。
- [ ] 推荐需求模板明确标为建议；规划正文不显示成已上线能力。
- [ ] 保持页面现有导航/布局风格，新增小节同步目录。
- [ ] 保留前端既有客服页面等修改，仅暂存 DocsView。

### Task 5：验证与交付

- [ ] 在 ai-service 执行 `uv run python -m compileall -q src`、`uv run pytest`、`uv lock --check`。
- [ ] 执行 `uv run python -m ai_service.orchestration.product_knowledge_evaluation`，确认 90 条和文档均通过校验。
- [ ] 前端运行三项现有 Node 测试、`npm run type-check` 和 `npm run build-only`。
- [ ] 探测本地服务；能获取已授权管理员会话及依赖时导入并真实评测，否则明确记录限制，不能用本地解析代替线上索引。
- [ ] 执行各仓库 `git diff --check`、`git status --short`，只提交任务文件，中文 Conventional Commits，报告两仓库哈希。

## 对设计的落地修正

逻辑元数据不提供运行时过滤保证：规划正文隔离在 planning 下，只有 documents 注册项允许导入。当前 6 条合成样例没有真实产品对应文档，不能与 90 条真实产品问题直接比较召回指标；它只保留为评测算法回归夹具。首次真实 90 条运行才是产品基线。90 条后续采用替换/版本递增维护，不额外扩充条数。
