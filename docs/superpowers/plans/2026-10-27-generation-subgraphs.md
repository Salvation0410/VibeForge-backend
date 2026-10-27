# 生成工作流子图实施计划

目标：按已批准设计，将生成分支和验证修复循环封装为子图，保留工具、事件、取消及发布语义。

采用当前工作区实施；初始工作区干净，当前分支为 dev。仅修改 Python 编排、必要测试和文档。

## 执行顺序

- [x] 提取 `WorkflowState`、路由、分组节点工厂和 Vue 工具循环；保留既有内部导入兼容。
- [x] 在 `orchestration/subgraphs/` 构建三类生成子图与验证修复子图，默认继承父图 saver。
- [x] 主图保留输入审核、上下文准备、发布和唯一成功终态；子图仅使用既有节点事件名称。
- [x] 增补父子图检查点命名空间、恢复不重复 Reviewer、清理和流式中途可见性测试。
- [x] 执行 `uv run python -m compileall -q src`、`uv run pytest`、`uv lock --check`（全量测试的既有 Reranker 环境失败见交接文档）。
- [x] 更新 README，编写交接文档，记录已实现边界、验证证据及下一阶段建议。
- [x] 检查 diff 与提交范围，以中文 Conventional Commits 提交。

## 验收约束

三类生成、默认最多两次修复、跨阶段工具预算、图片检查、取消、失败与发布规则保持一致。
源码快照和 Reviewer 原文不进入检查点。默认子图继承不代表生产自动续跑，也不提供 Vue 循环内部逐轮恢复。
检查点测试使用真实 LangGraph InMemorySaver；真实 PostgreSQL、模型和跨服务 SSE 必须单独标明是否执行。
