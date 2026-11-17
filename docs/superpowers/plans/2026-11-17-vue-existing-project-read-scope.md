# Vue 已有工程读取范围提示实施计划

> **For agentic workers:** 按用户确认的A在当前会话顺序执行，不派生代理。

**Goal:** 消除已有工程修改被引导为首次工程完整重写和反复确认的提示歧义。

**Architecture:** 只调整 generation.py 的Vue共用提示。generate与repair保留现有消息构造、严格解析、历史投影和工具子图；不增加自动收口或预算。设计取舍已由用户确认，见 doc/vue-generation-read-analysis-20261117.md 方案A。

**Tech Stack:** Python、pytest、LangChain注入式供应商适配器。

## 步骤与验证

- [x] 在 tests/test_openai_compatible.py 增加参数化实际消息测试，覆盖 first/existing/repair。注入工具JSON响应，断言system包含最新完整file_read复用、已有依赖无需重写、受影响核对与空toolCalls交回平台规则；human保留prompt/currentArtifact/toolResults，返回读取工具仍被接受。
- [x] 运行 `uv run pytest tests/test_openai_compatible.py -k existing_project_read_scope -q`，旧提示预期三项失败。
- [x] generation.py 修改前读取规则改为“若已有最新完整file_read结果则直接使用”，补充成功写入后或省略时的必要重读；首次创建保留入口和import目标完整写入要求，已有工程只写本次新增/修改文件；确认受影响导入/props/事件/样式后，齐备时空toolCalls交回构建/审查，不虚报构建。
- [x] 运行适配器、Vue子图、上下文和修复定向测试，确认新三项与旧严格解析/工具边界回归通过；执行compileall、完整pytest、uv lock --check。若本机共享GPU锁导致夹具失败，仅隔离手写Fake进程锁，不修改生产客服行为或配置；失败保留。
- [x] README新增当前提示范围说明，只暂存本轮段落，保留已有差异；主交接追加第43节，记录实现、触发场景、验证/失败、模型账本、清理与未测项。不将离线通过称为真实调用减少。
- [x] `git diff --check` 与暂存范围复核后中文Conventional Commit，不推送。只提交提示、适配器测试、本计划、README对应增量和交接。

## 约束

不修改解析器、24 KiB历史上限、40次共享工具预算、两次修复、每阶段格式重试、repair确定性收口、SSE或客服发布。首次计划、pendingFiles、配图、当前源码和用户原文仍优先。本次不新增真实生成采样；下一轮用一次明确范围的真实请求检查成本和页面功能，失败保留。
