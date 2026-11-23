# Python AI 专用版本实施计划

**Goal:** 独立完整项目的 AI 执行全部走 Python，Java 无 AI 框架依赖。
**Architecture:** Spring 业务与工具网关 + Python LangGraph 服务，保留既有公共协议。
**Tech Stack:** Java 21 / Spring Boot / Python 3.12 / FastAPI / LangGraph。

- [x] 检查源仓库、空目标目录、共用工具和旧框架引用；隔离克隆。
- [x] Java 源码及测试迁移：删除 LegacyAiGenerationGateway、DelegatingAiGenerationGateway、旧工厂和 langraph4j / dev/langchain4j；LangGraphAiGenerationGateway 成为唯一实现。
- [x] ImageSearchTool、ImageResource、ImageCategoryEnum 从旧图迁至业务包；移除框架注解。解除历史服务和消息 DTO 的框架类型依赖；JsonMessageStreamHandler 仅消费 Python 事件，保留展示与原始记录。
- [x] pom.xml 删除 Java AI 与 DashScope 依赖，显式加入原来传递提供的 Reactor；清理配置及旧专属测试，新增唯一网关/无框架回归测试。
- [x] 更新 README.md、AGENTS.md、ai-service/README.md、迁移说明和验证脚本，清楚区分当前架构和历史档案。
- [x] 执行 mvn -DskipTests compile 与适用 Java 离线回归；执行 Python compileall、pytest、uv lock --check；扫描依赖及残留，复核原仓库未改动。
- [x] git diff --check，审查改动并以中文 Conventional Commit 提交，记录哈希和本轮验证限制。

实现与复核按 subagent-driven-development 执行。验证以本轮输出为准；不调用真实收费模型。

本轮验证与限制见 [迁移说明](../../python-ai-only-migration.md)。先完成规格审查，再完成质量审查；工具开始事件回归已修复并复核。
