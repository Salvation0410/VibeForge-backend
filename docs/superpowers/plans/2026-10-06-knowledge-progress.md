# 知识库任务进度与 PyCharm 日志实施计划

**Goal:** 修复首次索引时区延迟，让管理员看见真实处理状态，并在 PyCharm 启动 Python 服务。

**Architecture:** 保留 Spring outbox 和 Python ETL 边界；调度时间统一 UTC，文档审计时间保留现有约定。前端复用现有接口和请求取消控制器，每 5 秒串行刷新，卸载时清理。

**Tech Stack:** Spring Boot、Vue 3、FastAPI、PyCharm。

- 修复 nextRetryTime 并覆盖非 UTC 主机回归；提供旧首次待处理任务的数据修复 SQL。
- 补充 Spring 任务开始、完成、错误码和重试日志；Python 输出索引开始、完成和失败类型，不输出令牌、签名 URL 或正文。
- 用户已确认前端方案：等待索引、处理中、完成、重试状态；任务详情自动刷新并显示 UTC 重试时间及错误原因。
- 添加 PyCharm 模块运行配置与中文使用步骤，释放旧服务端口，不在 Codex 启动 Python 服务。
- 验证相关 Java/Python 测试、干净编译、前端类型检查及构建，并记录未执行的真实索引验收。
