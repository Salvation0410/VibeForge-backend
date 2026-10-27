# 子图状态边界实施计划

> 在当前会话依次执行，承接交接文档优先项二。

**目标：** 限制子图输入/输出并分离 Vue 稳定上下文与工具历史，保持既有恢复及模型契约。

**架构：** 直接注册编译图，集中 schema；Vue 使用私有历史通道，入口拆分、模型调用/出口组装。

**技术栈：** Python 3.12、LangGraph 0.6.11、pytest、InMemorySaver。

- [x] 新增 `ai-service/tests/test_subgraph_state_boundaries.py`，覆盖输入/输出投影、checkpoint 顶层字段、私有历史写入和恢复、运行配置透传。
- [x] 执行 `uv run pytest tests/test_subgraph_state_boundaries.py -ra`，记录旧实现下的失败，确认测试捕获本轮缺口。
- [x] 新增 `orchestration/subgraphs/schemas.py` 的阶段 schema，修改 generation.py、verification_repair.py、vue.py 与 vue_tool_loop.py，使父子图字段按阶段传递。
- [x] 将 Vue 工具历史拆入私有通道；模型与出口恢复原 toolResults 契约，避免不必要的 context/历史写回。
- [x] 执行新测试及 API、子图、Vue 恢复、追踪专项，修复本轮回归；不改无关 Reranker 保护或本地 .env。
- [x] 更新 README、交接文档与验证记录，执行 compileall、uv lock --check、pytest -ra；若全量仍被 Reranker 锁影响，对照排除该模块的结果。
- [x] 审查 diff 和状态，完成 `git diff --check`，按中文 Conventional Commit 仅提交本次代码、测试和文档，报告哈希和验证边界。
