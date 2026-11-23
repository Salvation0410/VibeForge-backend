# Vue 修复质量优化 Implementation Plan

> **For agentic workers:** 使用 executing-plans 在当前会话逐项执行；用户已授权实施与交接，不额外派生任务。

**Goal:** 减少修复首次写入前的无效重读，定位有限组件事件错接，保留逐轮安全诊断。

**Architecture:** Vue 私有进度保存一次纠偏标志及计数；可信 AST 脚本新增事件模式并通过 Spring 构建边界执行。验证子图保存有界诊断历史，双白名单进入 PostgreSQL JSONB。

**Tech Stack:** Python/LangGraph、Node/Vue compiler、Java 21/JUnit。

## 1. 首次写入前读取保护

- [x] 新建 `ai-service/tests/test_vue_prewrite_guard.py` 覆盖首次写入前重复、纠偏后修复/失败、批次、取消及恢复；原实现先以工具预算耗尽失败，再实现保护通过。
- [x] `vue_read_stall.py` 提供 `read_repetition_stats(history, prefix)` 同时计算连续数及累计重复数；原 `repair_reads_stalled` 保留已写入要求。
- [x] `vue_tool_loop.py` 的私有进度新增一次纠偏标志和读数基线。完整纯读批次且未成功写入时，四次重复触发固定模型反馈，再四次重复抛 `VUE_REPAIR_READ_STALLED`。`subgraphs/vue.py` 阶段重置/出口清理反馈，默认字段兼容旧 checkpoint；纠偏后无写入的完成文本也明确失败。
- [x] 补充 `prompts/generation.py`、`prompts/review.py` 的事件链和锚点核对指引；不扩张用户需求。
- [x] 原读取保护、预算、阶段与新增保护/诊断联合测试通过；阶段测试加入旧读取反馈清除断言并另行通过。

## 2. 有限事件检查

- [x] 新建 `scripts/tests/vue-event-check.test.cjs`，用显式 `defineOptions({inheritAttrs:false})` 与运行时 `defineEmits(['add-to-cart'])` 检测父级 `@add`；默认透传只提示，原生/动态/attrs/类型/不完整输入跳过。先运行验证缺少实现而失败。
- [x] 在可信 `component-contract-check.cjs` 增加 `analyzeEvents` 和 `--events`，保持原模式协议；任何检查均不执行组件。
- [x] 新建 `VueEventCheck.java` 严格解析模式协议，`VueComponentContractCheck` 复用可信脚本调用边界，`VueProjectBuilder` 顺序改为属性→事件→锚点并组合摘要。Python routing 对环境错误直接失败。
- [x] Java 协议/构建顺序、失败短路/中断与 Python 路由测试通过；Node 三组 52 passed，Java 37 passed、1 原有集成项 skipped。

## 3. 逐轮观测与交接

- [x] 新建 `models/verification_diagnostics.py`：最多保留 36 项校验/构建/质量固定字段；不接纳正文/路径/任意代码。新候选保留历史但清空旧当前诊断。
- [x] 在 `nodes/verification.py`、验证私有 schema、`workflow.py` 和 `postgres_checkpoint.py` 接入并二次投影。读取重复计数仅入固定字段日志；诊断不入模型和公共事件。独立事务真实 PostgreSQL 验证缺字段保留及新历史替换，最终回滚。
- [x] 添加真实嵌套 Fake 图与投影测试，相关专项、compileall、uv lock --check、Java 指定测试及编译通过。Python 全量两轮及环境分组复核已执行，未取得单次全绿，完整计数和原因见交接文档，不掩盖失败。
- [x] 同步 `ai-service/README.md`、`AGENTS.md` 与 `doc/generation-quality-optimization-20261123-handoff.md`，交接区分历史证据、本轮验证、未测项、上线步骤及下一轮预算/浏览器工作。
- [x] 检查 `git diff --check`、提交范围和中文 Conventional Commits；本计划随实现一并提交，交付回复报告哈希。
