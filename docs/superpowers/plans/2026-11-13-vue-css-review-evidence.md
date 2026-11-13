# Vue 样式审查证据实施计划

**Goal:** 按已确认方案A降低样式存在却被拒绝的审查误判。

**Architecture:** 在现有共享Vue审查提示中明确CSS加载与作用域证据；不改变适配器、判定解析或修复流程。

**Tech Stack:** Python、pytest、现有OpenAI兼容模型代理。

- [x] 在 tests/test_vue_review_evidence.py 补正式单审查及三角色CSS证据要求的传播断言；固定快照包含全局入口导入和组件scoped，注入响应只验证协议，不能证明模型语义。
- [x] 在 prompts/review.py 共用说明追加全局/scoped、准确选择器、基础/变体及有界快照规则。定向执行 `uv run pytest tests/test_vue_review_evidence.py tests/test_openai_compatible.py tests/test_quality_review.py tests/test_quality_review_diagnostics.py tests/test_multi_agent_review.py`。
- [x] 用旧失败末轮的原快照及上下文，通过同一账本作一次新诊断审查。独占诊断标记，保存原响应，不能恢复旧任务或手改产物。
- [x] 同步 README 与交接；执行 compileall、uv lock --check、默认pytest，环境失败保持并另跑既有隔离脚本。
- [x] 关闭本轮临时服务及Redis进程，检查旧账本和既有修改，精确暂存并中文提交，不推送。
