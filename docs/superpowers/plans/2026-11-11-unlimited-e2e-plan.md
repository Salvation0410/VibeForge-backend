# 无次数上限真实验收与终态时序修复计划

> For agentic workers: 按现有测试集与本计划逐项核对；实现遵循 `executing-plans`，不并行修改共享运行服务。

**Goal:** 完成三类四属性、取消和基础设施复测，保留未触发分支与失败证据。

**Architecture:** 私有计数代理发送前落盘、无总次数上限；真实 UI/HTTP/SSE/模型/工具不替换。终态缓冲只调整 Python 流边界的发送时机。

**Tech Stack:** Chrome/Vite、Spring、FastAPI/LangGraph、DeepSeek、PostgreSQL、pytest。

- [x] 验证新授权，旧 60/20/30 账本不变；离线验证无上限计数超过 60 次及重启保持。
- [x] 执行 HTML/MULTI_FILE 四属性真实基线。
- [x] 完成 Vue 四属性真实验收；一份修复前报告采样失败保留并补验，修复后四属性直接通过。
- [x] 添加 `tests/test_stream_terminal_cleanup.py`；旧代码成功/失败两种门闩用例均失败。
- [x] 修改 `src/ai_service/orchestration/workflow.py`，将终态投递延后到任务结束。
- [x] 执行针对性回归、compileall、pytest、uv lock --check；最终 1254 passed、10 skipped。
- [x] 重启 Python，三类真实成功和取消复测；修复后 10 请求未出现原清理告警。
- [x] 扩展受控停止鉴权失败和 9 项真实 API 反例；自然无进展分支未触发。
- [x] 更新 AI README、AGENTS、测试集、交接及结果；仅将本次修复、测试与文档纳入交付提交。
