# 终态取消与传输收尾维护记录

**目标：** 按终态排空设计补齐确定性取消回归，修复已复现路径并验证真实 PostgreSQL 删除事务。

**范围：** Python 执行登记、事件入队、工作流排空、生成 HTTP 响应；不修改前端、Java、模型提示或预算。启动时已有的 `2026-11-11-cancellation-drain-regression.md` 为未跟踪文件，保留原样且不纳入本次提交。

- [x] 阅读 AGENTS、交接第 14 节、真实测试集、AI README 和终态设计；分别核对两仓库状态。
- [x] 在 `test_stream_terminal_cleanup.py` 加入门闩回归：旧代码 7 项新增失败，原 2 项通过。
- [x] 在 `test_generation_asgi_disconnect.py` 覆盖模型退出/正常清理 × ASGI 2.3/2.4：旧代码 4 项失败。
- [x] `active_generations.py` 限制首次取消并核对所有者；`events.py` 在终态入队前保护图收尾；`workflow.py` 排空后台任务并保持异常传播。
- [x] `generation_response.py` 与路由显式关闭两层迭代器；不再在内层清理完成后重建取消标记。
- [x] 增加终态后图写入、登记冲突、迟到所有者与观测异常回归。
- [x] 新建 `test_postgres_generation_drain_integration.py`：官方删除事务内门闩、独立连接核对、UUID 隔离及仅本轮清理；4 项新增与 2 项已有生命周期测试通过。
- [x] 完成 compileall、全量 pytest（1270 passed、14 skipped）、uv lock --check；同步最终交接与测试集。
- [x] 关闭本轮 PostgreSQL 容器，核对模型账本仍为 67；核对两仓库 Git 差异，仅将本轮文件列入提交范围。

常规命令在 `ai-service/` 执行：`uv run pytest -ra --tb=short`、`uv run python -m compileall -q src`、`uv lock --check`。真实数据库命令需显式设置 `AI_SERVICE_POSTGRES_INTEGRATION=true`，只运行新的事务测试及 `test_postgres_checkpoint_integration.py`；未执行其他子图恢复测试不计通过。
