# Vue 工具子图实施计划

> 按 executing-plans 在当前会话顺序实施，不创建额外聊天。

**目标：** 落实交接文档优先项一，生成与修复共用支持节点级检查点的 Vue 工具图。

**架构：** 私有循环状态、完整响应校验、单工具节点和继续判断；阶段出口限制回传字段；沿用请求依赖和父图 saver。

**技术栈：** Python 3.12、LangGraph 0.6.11、pytest、InMemorySaver。

- [x] 将 vue_tool_loop.py 中局部状态改为可序列化 VueLoopState；提取 model_turn、validate_turn、execute_tool、continue_loop 工厂，兼容内部旧入口。
- [x] 在 subgraphs/vue.py 注册共享循环图；生成直接调用，repair 分派复用，保留事件和工具 ID。复用 common.py 生命周期逻辑，输出不泄漏私有字段。
- [x] 新增 tests/test_vue_subgraph.py：真实 saver 暂停、重建、批次中间继续、修复预算、截断/无效批次、取消与 40 次工具上限。
- [x] 执行 `uv run pytest tests/test_vue_subgraph.py tests/test_subgraphs.py tests/test_api.py tests/test_langsmith_tracing.py -q`，检查失败直接原因并修正。
- [x] 同步 README 与交接文档。执行 `uv run python -m compileall -q src`、`uv lock --check`、`uv run pytest -ra`，必要时对照排除 Reranker 的结果。
- [x] 检查 `git diff --check`、`git status --short`、`git log -1 --pretty=%s`，仅提交本次文件，使用中文 Conventional Commit，报告哈希和实际测试结果。
