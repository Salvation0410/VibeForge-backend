# Vue 修复往返写入实施计划

> 已获用户“按照方案A执行”授权，在当前 dev 工作区顺序实施；保留指定既有修改，不再重复选择方案。

**Goal:** 以完整源码集合重复为依据，让修复工具循环及时交回原验证。

**Architecture:** 在 vue_tool_loop 私有进度保存成功写入批次标记与指纹；仅正式修复启用。使用现有 Spring 快照和 Python 指纹函数，原构建、审查和终态保持权威。

**Tech Stack:** Python、LangGraph、pytest、Fake SpringToolGateway、内存 checkpoint。

- [x] 新增 `ai-service/tests/test_vue_repair_write_cycle.py`：受控 A→B→A 父图用例先证明旧代码耗尽预算；保存失败日志，不改历史证据。
- [x] 修改 `ai-service/src/ai_service/orchestration/vue_tool_loop.py`：成功写入设置批次标记；整批完成后扫描、比较和选择 finish/model_turn；取消前后传播，失败不吞。
- [x] 修改 `ai-service/src/ai_service/orchestration/subgraphs/vue.py`：只有正式 repair 开启新 guard。生成及内部兼容入口继续关闭。
- [x] 补边界与 checkpoint 回归；原测试只对新增内部快照调用更新断言，保留文件工具预算断言。定向执行 `uv run pytest tests/test_vue_repair_write_cycle.py tests/test_vue_repair_read_stall.py tests/test_vue_subgraph.py tests/test_vue_repair_phase.py -ra --tb=short`。
- [x] 执行 `uv run python -m compileall -q src`、`uv lock --check` 和全量 pytest。本机服务可能持有默认重排器模型锁，沿用 ignored 测试进程隔离脚本，明确记录其身份和结果，不改 .env。
- [x] 同步 AI README、AGENTS、真实测试集与交接第 25 节：触发、实现、错误路径、局限、本轮证据和下一轮建议。核对账本、服务及两仓 Git 状态。
- [x] `git diff --check` 后显式暂存本轮文件，以中文 Conventional Commit 提交，不推送、不混入前端客服修改或旧未跟踪计划。

## 执行结果

旧代码三个回归失败，证据保留；最终定向 85 passed，隔离全量 1368 passed、14 skipped、2 warnings。compileall、uv lock --check 与空白检查通过。首次全量旧 API 序列断言失败后更新新增快照预期，保留模型工具预算断言并重跑全量。模型增量 0，未启停服务；提交范围仅本轮 13 个文件，不含原未跟踪计划或前端修改。详见交接第 25 节。
