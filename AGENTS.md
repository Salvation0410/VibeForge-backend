# AGENTS.md

本文件供后续 AI Agent 和开发协作者使用。修改代码前先阅读本文件，再结合任务查看相关源码、模块 README 和设计文档。不要把规划中的能力当成已经实现的能力。

## 项目概览

`yu-ai-code-mother` 当前由两个服务组成：

- Spring Boot 业务后端：负责用户与应用权限、聊天记录、文件安全、代码保存与构建、部署、下载、社区和管理后台，并维持面向前端的 SSE 协议。
- Python AI 服务：位于 `ai-service/`，使用 FastAPI、LangChain 和 LangGraph，负责模型调用、生成类型路由、工作流编排、质量检查、有限修复和工具调用决策。

Spring 是业务数据和项目文件的唯一所有者。Python 不访问 MySQL，也不直接操作生成项目目录；需要文件或构建操作时必须调用 Spring 内部工具网关。

主要技术栈：

- Java 21、Spring Boot 3.5.4、MyBatis-Flex、MySQL
- Redis、Spring Session、Redisson、Caffeine
- LangChain4j（迁移期 Legacy 回退）
- Python 3.12、FastAPI、LangChain、LangGraph、uv
- DeepSeek/OpenAI 兼容聊天模型
- Selenium、WebDriverManager、Knife4j、springdoc-openapi、Lombok

历史目录 `src/main/java/com/yupi/yuaicodemother/langraph4j` 是未接入当前主链路的 Java LangGraph4j 实验代码。不要将它与 `ai-service` 中实际运行的 Python LangGraph 混淆。

默认本地服务：

- Spring Boot：`http://localhost:8123/api`
- Python AI 服务：`http://localhost:8000`
- MySQL：`jdbc:mysql://localhost:3306/yu_ai_code_mother`
- Spring Redis：`localhost:6379/1`
- Python checkpoint Redis：默认 `localhost:6379/2`

## 常用命令

在仓库根目录执行 Spring 命令：

```powershell
mvn clean -DskipTests compile
.\mvnw.cmd compile
.\mvnw.cmd test
.\mvnw.cmd package -DskipTests
.\mvnw.cmd spring-boot:run
```

在 `ai-service` 目录执行 Python 命令：

```powershell
cd ai-service
Copy-Item .env.example .env
uv sync --frozen --python 3.12
uv run uvicorn ai_service.app:create_app --factory --host 0.0.0.0 --port 8000

uv run python -m compileall -q src
uv run pytest
uv lock --check
```

健康检查：

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

接口回归脚本位于 `scripts/`。运行前确认 Spring、MySQL、Redis 和任务需要的 Python AI 服务均已启动。

## 目录结构

Spring 业务后端：

- `src/main/java/com/yupi/yuaicodemother/controller`：HTTP 控制器，统一返回 `BaseResponse<T>`。
- `src/main/java/com/yupi/yuaicodemother/service`：业务接口与实现。
- `src/main/java/com/yupi/yuaicodemother/mapper`：MyBatis-Flex Mapper；XML 位于 `src/main/resources/mapper`。
- `src/main/java/com/yupi/yuaicodemother/model`：entity、DTO 和 VO。
- `src/main/java/com/yupi/yuaicodemother/common`、`exception`：通用响应和异常处理。
- `src/main/java/com/yupi/yuaicodemother/ratelimit`：限流注解、切面和 Redisson 配置。
- `src/main/java/com/yupi/yuaicodemother/ai`：Legacy LangChain4j 服务、工具和统一生成网关。
- `src/main/java/com/yupi/yuaicodemother/ai/gateway`：`AiGenerationGateway`、Legacy/LangGraph 实现及灰度委派。
- `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`：Python 调用的内部文件与构建工具边界。
- `src/main/java/com/yupi/yuaicodemother/core`：代码解析、保存、构建和流处理。
- `src/main/resources/prompt`：Legacy AI 系统提示词。

Python AI 服务：

- `ai-service/src/ai_service/app.py`：FastAPI 应用工厂和依赖组装入口。
- `ai-service/src/ai_service/config.py`：`AI_SERVICE_*` 配置。
- `ai-service/src/ai_service/api`：鉴权依赖、内部路由和请求响应模型。
- `ai-service/src/ai_service/orchestration`：LangGraph 工作流、事件和协作式取消。
- `ai-service/src/ai_service/models`：模型协议与 OpenAI 兼容适配器。
- `ai-service/src/ai_service/infrastructure`：Redis checkpoint 和 Spring 工具网关客户端。
- `ai-service/tests`：Fake Model 驱动的单元与契约测试，不访问真实模型。

其他目录：

- `sql`：初始化和增量 SQL。
- `doc`：面向项目的设计、启动、接口和交接文档。
- `docs/superpowers`：实施过程中的设计与执行计划。
- `scripts`：本地接口测试和数据脚本。
- `projects`：用户或工具生成的本地产物，默认不要修改或提交。

## 核心业务模块

- 用户模块：`SysUserController`、`SysUserService`、`SysUserServiceImpl`。
- 应用模块：`AppController`、`AppService`、`AppServiceImpl`。
- 聊天历史：`ChatHistoryController`、`ChatHistoryService`、`ChatHistoryOriginalService`、`ChatHistoryExportService`。
- 社区模块：`CommunityPostController`、`CommunityCommentController`、`CommunityTagController` 及对应 Service。
- 管理概览：`AdminDashboardController`。
- 部署静态资源：`StaticResourceController`。

应用与代码生成链路：

- 对外入口：`AppController` 的 `/apps/chat/gen/code`，实际路径为 `/api/apps/chat/gen/code`。
- 统一 AI 边界：`AiGenerationGateway`。
- 引擎选择：`DelegatingAiGenerationGateway`，支持 `legacy`、`langgraph`、`gray`/`auto`。
- Legacy：`LegacyAiGenerationGateway -> AiCodeGeneratorFacade -> LangChain4j`。
- 新链路：`LangGraphAiGenerationGateway -> Python /internal/v1/generations:stream`。
- 工具边界：Python 通过 `/api/internal/ai-tools/invoke` 调用 Spring 文件和构建能力。
- 保存、解析、构建：`core/saver`、`core/paser`、`VueProjectBuilder`。

## AI 请求链路

```text
前端 EventSource
  -> AppController
  -> AppServiceImpl
  -> AiGenerationGateway
     -> LegacyAiGenerationGateway
     或
     -> LangGraphAiGenerationGateway
        -> Python FastAPI NDJSON
        -> LangGraph StateGraph
        -> 模型 / SpringToolGateway
  -> StreamHandlerExecutor
  -> 前端 SSE
```

Python 工作流包含输入校验、上下文准备、HTML/多文件/Vue 分支、产物校验、项目构建、质量检查、最多两次修复和完成事件。Vue 分支存在工具循环和最大调用次数限制。

内部事件为 `content_delta`、`tool_started`、`tool_finished`、`node_status`、`completed`、`failed`。Java 网关将 NDJSON 转换为现有流处理器可消费的格式，对外 SSE 协议保持不变。

## 配置与令牌

Spring 配置：

```yaml
ai:
  engine: ${AI_ENGINE:legacy}
  service-url: ${AI_SERVICE_URL:http://localhost:8000}
  token: ${AI_SERVICE_INTERNAL_BEARER_TOKEN:}
```

Python 配置从 `ai-service/.env` 加载，示例见 `.env.example`。当前两个调用方向共用 Spring 的 `ai.token`，本地联调时以下三处必须一致：

```text
Python AI_SERVICE_INTERNAL_BEARER_TOKEN
= Python AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN
= Spring AI_SERVICE_INTERNAL_BEARER_TOKEN
```

这些值是自行生成的高强度静态共享密钥，没有自动过期能力。不要提交真实令牌、DeepSeek API Key、OSS 密钥、邮件授权码或 `.env`。生产环境使用密钥管理服务或运行时环境变量，并定期轮换。

## 编码约定

- 新增业务接口遵循 Controller -> Service -> Mapper 分层。
- Controller 返回 `ResultUtils.success(data)` 或抛出 `BusinessException`。
- 参数校验使用 `ThrowUtils.throwIf(...)` 或 `BusinessException(ErrorCode.PARAMS_ERROR, ...)`。
- 权限控制使用 `@AuthCheck`；管理员接口使用 `UserConstant.ADMIN_ROLE`。
- 需要限流的接口使用 `@RateLimit`。
- 数据访问优先使用 MyBatis-Flex；复杂 SQL 放入 mapper XML。
- 新增数据库字段时同步更新 entity、DTO/VO、mapper XML、迁移 SQL 和测试。
- Java AI 改动通过 `AiGenerationGateway` 边界，不在 Controller 中直接调用模型。
- Python API、编排、模型和基础设施分别放入现有四类包，不重新堆回顶层目录。
- Python 不直接访问 MySQL 或项目目录；文件操作必须经过 Spring 工具网关。
- 修改代码生成类型时检查 `CodeGenTypeEnum`、Python `CodeGenType`、Parser、Saver、Builder、Gateway 和 Workflow。
- 保持公共 SSE 协议兼容；内部 Spring/Python 使用 NDJSON。
- 不大面积重写历史乱码注释，不进行无关重构。
- 不提交生成文件、日志、下载产物、`.venv`、`.env` 或 `projects/`。

## 工具边界与安全

`InternalAiToolsController` 当前提供文件读取、目录读取、写入、修改、删除、产物非空校验和项目构建。所有调用必须通过 Bearer 认证，携带 `toolCallId` 和 `appId`，使用固定应用沙箱和相对路径，并禁止绝对路径、目录穿越和删除受保护文件。

当前工具幂等结果保存在 Spring 进程内 `ConcurrentHashMap`，服务重启后丢失，也不支持多实例共享。原方案中的 Redis 幂等尚未实现，不要误称已经实现。

## API 与响应规范

Spring 对外接口使用统一响应：

```json
{
  "code": 0,
  "data": {},
  "message": "ok"
}
```

接口路径需叠加全局 `/api` 前缀。Python 内部接口不使用 Spring `BaseResponse`，其模型位于 `ai-service/src/ai_service/api/schemas.py`。

## 数据库

- 初始化 SQL：`sql/init_database.sql`
- 增量 SQL：`sql/alter_*.sql`

做数据库修改时检查实体、表字段、Mapper、Service、VO 聚合、默认值、索引、逻辑删除、审核状态和时间字段。Python AI 服务不得直接连接业务 MySQL。

## 测试策略

Java 改动：

```powershell
.\mvnw.cmd test -Dtest=ClassNameTest
mvn clean -DskipTests compile
```

Python AI 改动：

```powershell
cd ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

当前 Python 测试覆盖鉴权、健康检查、三类生成分支、事件顺序、最多两次修复、Vue 工具上限、取消、Redis 降级、Spring 工具网关和包导入。真实模型、真实 Redis、Spring 文件构建和完整 SSE 仍需要集成或端到端环境验证。

AI、截图、OSS、邮件、浏览器驱动和真实模型测试可能依赖本地环境、网络、密钥或 Chrome。无法运行时必须说明失败命令和直接原因。

## 交付前检查

- 运行 `git status --short`，只提交任务相关文件；保留用户未跟踪文件。
- Java 改动至少执行干净编译和相关测试。
- Python 改动执行 compileall、pytest 和 `uv lock --check`。
- 跨服务接口变更同步检查 Java 事件适配、Python schema、README 和设计文档。
- 配置改动检查真实密钥、个人路径和生产地址。
- 代码生成链路改动检查三种生成类型、事件顺序、修复上限、取消和工具调用。
- 使用 `git diff --check` 检查空白错误。
- 不声称 Docker、真实模型或端到端流程通过，除非本轮确实执行并记录结果。

## 相关文档

- `ai-service/README.md`：AI 服务完整中文使用说明。
- `doc/ai-service-startup.md`：AI 服务详细启动和联调说明。
- `doc/ai-service-langchain-langgraph-refactor-design.md`：重构方案设计。
- `doc/ai-service-phase-one-handoff.md`：第一阶段重构交接说明。

## 给后续 Agent 的建议

- 先使用 `rg` 搜索实际调用链和配置，再改代码。
- 先区分当前实现、设计目标和历史实验代码。
- 小范围修改优先，不删除或恢复用户未明确要求的文件。
- 遇到外部依赖失败时记录证据，不假装验证通过。
- 跨 Java/Python 修改时列清接口、配置、测试和回滚影响。
- 保持中文业务文档和注释风格；Java 包名、Python 模块名和配置键使用既有英文命名。
