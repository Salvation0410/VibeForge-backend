# AGENTS 指南与 AI 重构交接文档设计

## 目标

全面更新根目录 `AGENTS.md`，使其准确反映当前 Spring Boot + Python AI 微服务架构；同时新增 `doc/ai-service-phase-one-handoff.md`，为下一轮 AI 对话提供第一阶段重构的可验证上下文、关键文件索引、运行方式、已知限制和后续建议。

## AGENTS.md 更新范围

保留仍有效的用户、应用、聊天历史、社区、管理后台、数据库和通用编码规范，更新以下内容：

- 将标题修正为 `AGENTS.md`。
- 项目概览改为 Spring Boot 业务后端与 Python AI 服务双服务架构。
- 技术栈增加 Python 3.12、FastAPI、LangChain、LangGraph、uv 和 Redis checkpoint。
- 默认服务增加 Python `8000` 端口及 Redis database 2。
- 常用命令增加 AI 服务依赖安装、启动、测试、语法检查和锁文件校验。
- 目录结构增加 `ai-service` 四类职责包、AI 网关、工具控制器和相关文档。
- AI 模块说明改为 `AiGenerationGateway` 统一入口、Legacy/LangGraph/gray 引擎选择和内部 NDJSON。
- 配置安全增加静态服务令牌关系、环境变量注入和不得提交 `.env` 的约束。
- 测试与交付检查增加 Python 14 项测试、Java 编译、旧 import 检查及跨服务契约核对。
- 明确 LangGraph4j 是历史实验代码，不是当前 Python LangGraph 生产链路。

## 交接文档结构

`doc/ai-service-phase-one-handoff.md` 包含：

1. 文档用途和当前基线。
2. 第一阶段目标与完成范围。
3. 当前架构和端到端请求流。
4. Spring 与 Python 职责边界。
5. Java、Python、配置和文档关键文件索引。
6. LangGraph 三类生成分支、事件协议和工具调用边界。
7. 配置项、两个令牌的关系、Redis 策略和启动顺序。
8. 已运行的验证命令与最近结果。
9. 第一阶段相关 Git 提交记录。
10. 已知限制、实现差距和风险。
11. 下一阶段建议任务及优先级。
12. 下一轮 AI 开始前的检查清单和不可触碰范围。

## 真实性约束

- 只将仓库现有代码和已验证命令写为“已完成”。
- 设计方案中存在但代码未实现的能力必须写入“已知限制”或“后续任务”。
- 明确当前工具幂等缓存是 Spring 进程内 Map，不是原方案预期的 Redis 幂等。
- 明确 Spring 当前两个调用方向共用 `ai.token`，本地联调时两个 Python Bearer 配置使用同一随机令牌。
- 明确客户端断连取消为协作式取消，且不提供断线重连、后台续跑和人工审批。
- 不记录真实 API Key、Bearer 令牌或其他敏感值。

## 验证

- 对照实际目录、`application.yml`、`.env.example`、Java 网关和 Python 路由检查文档内容。
- 使用 `git log` 校验提交哈希和提交标题。
- 运行 `uv run pytest`、`uv lock --check` 和 `mvn clean -DskipTests compile`，在交接文档中记录本轮新鲜结果。
- 检查所有文档链接目标存在。
- 运行 `git diff --check`，并确认只修改目标文档。

## 非目标

- 不修改 Java、Python、配置或数据库实现。
- 不清理 Legacy LangChain4j 或历史 LangGraph4j 代码。
- 不执行密钥轮换、Docker 构建、真实模型请求或端到端浏览器测试。
- 不提交或修改用户已有的 `projects/` 未跟踪文件。
