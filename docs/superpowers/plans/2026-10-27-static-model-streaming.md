# 静态模型流式实施计划

**目标：** HTML/MULTI_FILE 生成与修复实时展示文本，最终历史和发布继续使用完整候选。

**架构：** 可选模型流回调、NDJSON contentMode、Java 内部展示/候选帧；公共 SSE 不变。

**技术栈：** Python 3.12、ChatOpenAI、LangGraph、Java 21、Reactor、pytest/JUnit。

- [x] 新增 Python 模型流与工作流契约测试，复现旧实现尚未流式的缺口。
- [x] 在 models/base.py 定义可选流式协议，openai_compatible.py 实现块合并/终态检查/关闭；保留旧方法和 Vue 完整响应。
- [x] 提取静态模型调用辅助函数，接入生成和修复节点，区分展示与完整候选，取消与截断不发布。
- [x] 新增 Java 网关和 handler 测试，实现内部展示/候选帧，保留旧调用兼容，阻止增量污染最终历史。
- [x] 同步 Python schema、README、跨服务设计与交接说明，说明部署顺序及生产恢复边界。
- [x] 运行 compileall、uv lock --check、Python 专项及全量；运行 Java clean compile 和相关测试；运行前端流式测试、类型检查和 build-only，记录外部依赖失败直接原因。
- [x] 检查两仓库状态，保留前端既有改动；git diff --check 后仅提交本次后端/Python/文档，中文 Conventional Commit，报告验证及哈希。
