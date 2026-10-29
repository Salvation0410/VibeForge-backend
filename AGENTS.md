# AGENTS.md

本文件供后续 AI Agent 和开发协作者使用。修改代码前先阅读本文件，再结合任务查看相关源码、模块 README 和设计文档。不要把规划中的能力当成已经实现的能力。

最近一次源码核对：2026-10-29。根 README 面向使用者，本文件面向开发协作，`ai-service/README.md` 描述 Python 运行细节；修改相关行为时同步对应文档，不将历史验收结果写成本轮已验证。

## 项目概览

`yu-ai-code-mother` 当前由两个服务组成，并配套一个独立前端仓库：

- Spring Boot 业务后端：负责用户与应用权限、聊天记录、文件安全、代码保存与构建、部署、下载、社区和管理后台，并维持面向前端的 SSE 协议。
- Python AI 服务：位于 `ai-service/`，使用 FastAPI、LangChain 和 LangGraph，负责模型调用、生成类型路由、工作流编排、质量检查、有限修复和工具调用决策。
- Vue 前端：默认位于同级目录 `D:/VibeForge/yu-ai-code-mother-frontend`，负责应用生成对话、SSE 消费、预览、部署、下载和社区界面。它是独立 Git 仓库，不要在后端仓库中假定其工作树状态。

Spring 是业务数据和项目文件的唯一所有者。Python 不访问 MySQL，也不直接操作生成项目目录；需要文件或构建操作时必须调用 Spring 内部工具网关。

主要技术栈：

- Java 21、Spring Boot 3.5.4、MyBatis-Flex、MySQL
- Redis、Spring Session、Redisson、Caffeine
- LangChain4j（迁移期 Legacy 回退）
- Python 3.12、FastAPI、LangChain、LangGraph、uv
- DeepSeek/OpenAI 兼容聊天模型
- Selenium、WebDriverManager、Knife4j、springdoc-openapi、Lombok

历史目录 `src/main/java/com/yupi/yuaicodemother/langraph4j` 中的 Java LangGraph4j 工作流未接入当前生成主链路，不要与 `ai-service` 的 Python LangGraph 混淆；其中 `tools/ImageSearchTool` 等工具仍被当前内部网关复用，不能将整个目录视为可删除代码。

默认本地服务：

- Spring Boot：`http://localhost:8123/api`
- Python AI 服务：`http://localhost:8000`
- MySQL：`jdbc:mysql://localhost:3306/yu_ai_code_mother`
- Spring Redis：`localhost:6379/1`
- Python checkpoint PostgreSQL：默认数据库 `yu_ai_checkpoint`，本地 Docker 通常映射到 `localhost:5432`
- Vue 前端：`http://localhost:5173`

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
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv sync --frozen --python 3.12
uv run python -m ai_service.server

uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Windows 优先使用 `ai_service.server`，它为 psycopg 异步 checkpoint 配置 SelectorEventLoop。只在 `.env` 不存在时复制样例，避免覆盖已有配置；本机运行还需把样例的 `host.docker.internal` 工具网关地址改成 `localhost`，并统一双向令牌。

健康检查：

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

在同级前端仓库执行：

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-frontend
node --test --experimental-strip-types tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts
npm run type-check
npm run build-only
```

接口回归脚本位于 `scripts/`。运行前确认 Spring、MySQL、Redis 和任务需要的 Python AI 服务均已启动。

## 目录结构

Spring 业务后端：

- `src/main/java/com/yupi/yuaicodemother/controller`：HTTP 控制器；普通 JSON 接口返回 `BaseResponse<T>`，SSE、下载和静态资源按各自协议返回。
- `src/main/java/com/yupi/yuaicodemother/service`：业务接口与实现。
- `src/main/java/com/yupi/yuaicodemother/mapper`：MyBatis-Flex Mapper；XML 位于 `src/main/resources/mapper`。
- `src/main/java/com/yupi/yuaicodemother/model`：entity、DTO 和 VO。
- `src/main/java/com/yupi/yuaicodemother/common`、`exception`：通用响应和异常处理。
- `src/main/java/com/yupi/yuaicodemother/ratelimit`：限流注解、切面和 Redisson 配置。
- `src/main/java/com/yupi/yuaicodemother/ai`：Legacy LangChain4j 服务、工具和统一生成网关。
- `src/main/java/com/yupi/yuaicodemother/ai/gateway`：`AiGenerationGateway`、Legacy/LangGraph 实现及灰度委派。
- `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java`：Python 调用的内部文件与构建工具边界。
- `src/main/java/com/yupi/yuaicodemother/core`：代码解析、保存、构建和流处理。
- `src/main/java/com/yupi/yuaicodemother/core/artifact`：不可变产物、校验、源码快照、MySQL 代际与活动指针。
- `src/main/java/com/yupi/yuaicodemother/service/impl/GenerationTaskServiceImpl.java`：任务台账、执行所有者登记与心跳续写。
- `src/main/java/com/yupi/yuaicodemother/config/GenerationTaskHeartbeatConfiguration.java`：默认任务和生成心跳的独立调度器。
- `src/main/resources/prompt`：Legacy AI 系统提示词。

Python AI 服务：

- `ai-service/src/ai_service/app.py`：FastAPI 应用工厂和依赖组装入口。
- `ai-service/src/ai_service/config.py`：`AI_SERVICE_*` 配置。
- `ai-service/src/ai_service/api`：鉴权依赖、内部路由和请求响应模型。
- `ai-service/src/ai_service/orchestration`：LangGraph 工作流、事件和协作式取消。
- `ai-service/src/ai_service/orchestration/subgraphs`：静态生成、Vue 工具循环和验证修复子图，以及独立状态 schema。
- `ai-service/src/ai_service/orchestration/nodes`：输入、生成、验证和发布的节点实现。
- `ai-service/src/ai_service/models`：模型协议与 OpenAI 兼容适配器。
- `ai-service/src/ai_service/models/vue_context.py`：仅面向模型消息的 Vue 工具历史投影，不裁剪持久化记录。
- `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json`：Spring/Python 共用工具契约；变动时检查 Java 枚举、schema 与两侧测试。
- `ai-service/src/ai_service/infrastructure`：PostgreSQL checkpoint、初始化命令和 Spring 工具网关客户端。
- `ai-service/tests`：Fake Model 驱动的单元与契约测试，不访问真实模型。

其他目录：

- `sql`：初始化和增量 SQL。
- `doc`：面向项目的设计、启动、接口和交接文档。
- `docs/superpowers`：实施过程中的设计与执行计划。
- `scripts`：本地接口测试和数据脚本。
- `projects`：用户或工具生成的本地产物，默认不要修改或提交。

独立 Vue 前端仓库的相关文件：

- `src/pages/AppChatView.vue`：生成对话、停止操作、预览状态和最终刷新。
- `src/api/app.ts`：SSE 连接与 `business-error` 事件适配。
- `src/utils/generationStreamProgress.ts`：在 Vue 响应式状态之外维护流式进度和有限文本窗口。
- `src/utils/previewRefreshCoordinator.ts`：确保仅在当前生成成功后刷新一次预览。
- `src/utils/optimizePrompt.ts`：按生成类型提供简短、普通用户可理解的优化提示。
- `tests`：前端纯函数和流式状态测试。

## 核心业务模块

- 用户模块：`SysUserController`、`SysUserService`、`SysUserServiceImpl`。
- 应用模块：`AppController`、`AppService`、`AppServiceImpl`。
- 聊天历史：`ChatHistoryController`、`ChatHistoryService`、`ChatHistoryOriginalService`、`ChatHistoryExportService`。
- 社区模块：`CommunityPostController`、`CommunityCommentController`、`CommunityTagController` 及对应 Service。
- 管理概览：`AdminDashboardController`。
- 生成任务：`GenerationTaskService`、`GenerationTaskMapper`、`GenerationTaskVO`；通过 AppController 查询。
- 客服与知识管理：`CustomerServiceController`、`CustomerServiceKnowledgeAdminController`、`CustomerServiceKnowledgeEtlWorker`；Python 负责 ETL、Milvus 检索、重排和引用校验。
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

Python 主图负责输入审核、上下文准备、三个生成子图调度、验证修复子图、静态发布与唯一成功终态。节点实现放在 `nodes/`，阶段输入/内部/输出状态放在 `subgraphs/schemas.py`，不得重新把完整父状态传入子图。子图继承父 checkpointer 与 `{appId}:{requestId}`，不启用跨请求 Agent 记忆。默认最多修复两次（配置可调），Vue 默认总模型工具预算为 40 次，生成和修复共用。

LangGraph 链路的 HTML/MULTI_FILE 生成与修复支持供应商模型文本流；`content_delta.data.contentMode` 区分 `display_delta` 与 `candidate`，展示分片不得拼成待发布候选或写入最终历史。只有供应商正常结束后才传递完整 candidate；Vue 工具 JSON、路由和质量审查继续使用完整模型响应。

内部事件为 `content_delta`、`tool_started`、`tool_finished`、`node_status`、`completed`、`failed`。Java 网关将 NDJSON 转换为现有流处理器可消费的格式，对外 SSE 协议保持不变。

HTML 和 MULTI_FILE 只有通过 Spring 的严格解析、确定性校验和不可变版本发布后才能完成。HTML 默认还会执行 Selenium 浏览器烟测；发布失败、模型长度截断或页面运行检查失败时保留上一活动版本。`GenerationLeaseService` 保留 Redis 入口串行化与实时状态，取消和静态提交资格以 MySQL `generation_arbitration` 为权威；静态发布最终按当前 fence token 更新数据库活动指针。Vue 仍直接修改工程目录，没有同等版本回滚保证。

前端仍实时展示流式输出，但只在非响应式累加器中保留最近 2000 个字符，每 80ms 最多提交一次 Vue 快照，同时显示累计字符数。完成后重新读取服务端聊天历史，不能把这 2000 个字符的临时窗口当成最终完整消息。预览在生成期间保留旧版本，只在当前请求成功后刷新一次；失败、停止和迟到回调不得刷新预览。

## 配置与令牌

Spring 配置：

```yaml
ai:
  engine: ${AI_ENGINE:langgraph}
  service-url: ${AI_SERVICE_URL:http://localhost:8000}
  token: ${AI_SERVICE_INTERNAL_BEARER_TOKEN:}
```

以上为公共 `application.yml` 默认值，默认 profile 为 `local`；`AiEngineProperties` 类字段的 Legacy 兜底不代表仓库启动默认引擎。本地配置或环境变量可以覆盖公共值。Spring 不自动读取 Python `.env`。

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
- AI 服务（`ai-service/`）的 API、配置、编排、模型、基础设施、启动方式或运行行为发生变动时，按影响范围同步更新 `ai-service/README.md`；文档只描述当前已实现并可由代码或测试验证的能力，不把设计规划写成现状。
- 生成或新增的代码必须包含与复杂度相匹配的必要注释；注释统一使用中文，说明设计意图、边界条件、并发/安全约束或非显然的业务规则，禁止用注释重复直白代码。修改现有代码时，将涉及范围内的英文注释改为中文。
- Python 不直接访问 MySQL 或项目目录；文件操作必须经过 Spring 工具网关。
- 修改代码生成类型时检查 `CodeGenTypeEnum`、Python `CodeGenType`、Parser、Saver、Builder、Gateway 和 Workflow。
- 保持公共 SSE 协议兼容；内部 Spring/Python 使用 NDJSON。
- 静态流改动同时检查 `StaticGenerationStreamFrame`、网关、SimpleTextStreamHandler 的展示/候选分离，禁止把修复草稿覆盖最终候选。
- Vue 每轮默认最多两个工具，非法 JSON 每阶段最多额外重试两次，不执行失败轮工具；生成和修复共用总工具预算，不因子图切换重置。
- Vue 模型工具历史按两层 JSON 转义字节上界限制为 24 KiB，优先保留近期完整结果并标记旧结果省略/失效；不得裁剪成半份源码或把模型投影视为持久化历史。最新结果放不下应返回 `VUE_CONTEXT_BUDGET_EXCEEDED`。
- 输入审核仅在 LangGraph 链路生效，默认开启；规则预算检查不得随语义审核开关关闭。通用上下文窗口默认 65536、输出 8192、协议预留 4096，本地覆盖值不是默认值；扩容不提高工具历史上限。
- LangGraph 请求只传最近最多六条用户需求（每条最多 1000 个 UTF-16 字符）及最多 2000 字符的初始需求；不要声称传递了全部历史源码或实现了跨请求长期记忆。
- Vue 多 Reviewer 开关默认关闭；真实源码快照最多 24 文件、每文件 12000 字符、总计 60000 字符，不得承诺全项目完整审查。
- 前端不得把完整流式源码逐分片拼接进 Vue 响应式状态；调整生成展示时保留 2000 字符窗口、80ms 刷新上限和终态回源语义。
- “优化提示”必须使用普通用户能理解的简短文案，并按 HTML、MULTI_FILE、VUE_PROJECT 区分完整产物要求；三类提示都必须保留原有功能、文字、图片和操作方式，禁止残缺结果和额外解释。
- 固定尺寸加载图位于 Flex 容器时必须禁止收缩并保持 1:1 比例，避免长提示文字将圆形挤成椭圆。
- 不大面积重写历史乱码注释，不进行无关重构。
- 不提交生成文件、日志、下载产物、`.venv`、`.env` 或 `projects/`。
- Git 提交信息必须使用中文，并符合 Conventional Commits 规范；格式为 `type(scope): 中文说明`，例如 `feat(ai): 接入LangSmith脱敏追踪`。提交前检查 `git log -1 --pretty=%s` 和 `git diff --check`，禁止使用无意义或纯英文提交信息。
- 凡涉及代码改动，完成必要验证后必须提交 Git，不能仅将修改留在工作区。提交范围仅包含本次任务改动及其必要测试、文档，不得混入用户既有或无关修改；后端与前端分别在各自仓库提交。交付时报告提交哈希及验证结果，若验证或提交受阻必须明确说明原因。

## 工具边界与安全

`InternalAiToolsController` 当前提供目录/文件读写修改删除、图片搜索、产物上下文/校验/发布、项目构建及 Vue 源码快照。所有调用必须通过 Bearer 认证，携带 `requestId`、`toolCallId` 和 `appId`；文件操作使用固定应用沙箱和相对路径，并禁止绝对路径、目录穿越和删除受保护文件。只有目录/文件工具及图片搜索对模型开放，其他能力由工作流调用。`vue_source_snapshot` 瞬时读取，不写 Redis 成功结果缓存。

内部工具幂等状态和成功结果通过 Spring Redisson 保存在 Redis，作用域为
`appId + requestId + toolCallId`，支持跨 Spring 实例共享；文件系统操作与 Redis
状态写入并非同一事务，因此 `RUNNING` 和写回失败仍按不确定状态处理，不承诺严格 exactly-once。

HTML 和 MULTI_FILE 发布使用 `VersionedArtifactStore` 保存不可变 release、manifest、请求墓碑和活动指针；它与上述内部工具调用结果缓存不是同一套幂等机制。不要把版本发布幂等误写成工具网关已经实现 Redis 幂等。

新静态发布协议的权威活动指针在 MySQL `generation_active_artifact`，提交历史在 `generation_publication`；`.current` 只是兼容镜像。启用 `.db-pointer-enabled` 的项目在数据库不可用时失败关闭，不回退旧文件指针。最终激活必须核对请求仲裁与当前代际，不能仅凭 Redis 租约授权。新协议暂未自动清理 release；不得沿用旧文件协议的清理逻辑删除其他代际版本，也不得手动删除启用标记或重置代际记录。

## 生成任务、心跳与恢复边界

- `/api/apps/chat/gen/code/task` 查询任务台账；只允许现存应用所有者查询本人任务，省略 requestId 返回最近未过期记录。终态查询窗口为 30 天，无物理清理调度。
- 台账覆盖 Legacy/LangGraph 与三种生成类型；记录执行状态、稳定错误码和时间，不保存提示词、源码或凭据。MySQL、Redis、文件系统之间没有跨资源事务。
- 查询分别展示 `executionState`、`leaseState`、`publicationState` 与 `heartbeatState`，不是原子快照；`COMMITTING` 不等于发布，`COMMITTED` 不等于当前活动版本，Vue 返回 `NOT_VERSIONED`。
- 随机执行所有者只登记本实例已确认插入的任务。独立调度器每 15 秒按 owner/token/当前代际续写心跳，数据库观测期限为 90 秒；不扫描 RUNNING 行自动认领。
- `LIVE/EXPIRED/SUPERSEDED/UNKNOWN/TERMINAL` 只用于心跳观测。过期不改执行终态、不触发取消或恢复；原所有者真实终态仍可条件落账。取消请求与浏览器断开不提前结束心跳，需等待引擎真实终态。
- 取消与静态提交资格由持久化仲裁决定，取消时间不代表引擎副作用已停止；Redis 不可用时没有新增离线取消入口。
- 生产恢复调度、执行认领、Python 自动续跑与 SSE 重连未实现。不得根据 RUNNING、EXPIRED、无锁或测试中的 `ainvoke(None)` 自动恢复。

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

已有库升级生成链路时，依次执行 `alter_generation_task.sql`、`alter_generation_fencing.sql`、`alter_generation_arbitration.sql`、`alter_generation_execution_heartbeat.sql`；新建库的 `init_database.sql` 已包含这些结构。Spring 不自动迁移，缺少结构时在模型调用前失败关闭。升级前排空旧实例执行，统一切换支持数据库指针的版本；不能直接混跑旧协议实例或直接回退旧代码。具体回滚与数据核对见 `doc/generation-task-ledger.md`。

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

Python 常规测试使用 Fake/注入式 Provider，覆盖鉴权、健康检查、三类生成、子图状态边界、静态文本流、Vue 工具循环及上下文预算、输入审核、有限修复、取消、checkpoint 降级、工具契约、客服 RAG/ETL 和旁路追踪。常规离线测试不能替代真实模型、文件构建或完整 SSE 验收。

真实资源测试均需显式 opt-in，先阅读对应夹具，使用隔离数据；不要为文档更新默认开启：

| 场景 | 开关 | 测试入口 |
| --- | --- | --- |
| PostgreSQL 生命周期/子图跨连接恢复 | `AI_SERVICE_POSTGRES_INTEGRATION=true` | `tests/test_postgres_checkpoint_integration.py`、`tests/test_postgres_subgraph_integration.py` |
| Redis 工具幂等多实例 | `AI_REDIS_INTEGRATION=true` | `ToolInvocationIdempotencyRedisIT` |
| MySQL/Redis 任务、仲裁、心跳与发布 | `AI_TASK_INTEGRATION=true` | `GenerationTaskResourcesIT` |

PostgreSQL 连接读取 Python Settings；Java 任务夹具只接受本地回环地址的业务库 `yu_ai_code_mother` 与 Redis database 1。真实资源测试里的模型/工具仍可能是 Fake，不能写成完整端到端通过。命令与限制见 `ai-service/README.md`、`doc/generation-task-ledger.md`。

Python checkpoint 已使用独立 PostgreSQL 数据库；Spring 工具幂等仍使用 Redis database 1。长期记忆未启用，不要把 checkpoint 表当作 `PostgresStore` 或跨请求知识库使用。

AI、截图、OSS、邮件、浏览器驱动和真实模型测试可能依赖本地环境、网络、密钥或 Chrome。无法运行时必须说明失败命令和直接原因。

前端改动：

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-frontend
node --test --experimental-strip-types tests/optimizePrompt.test.ts tests/generationStreamProgress.test.ts tests/previewRefreshCoordinator.test.ts
npm run type-check
npm run build-only
```

当前前端测试覆盖 2000 字符尾部窗口、80ms 合并刷新、终态刷新与销毁、三类简短优化提示、图片保护和单次预览刷新。真实模型生成仍需在 Spring、Python 和前端都可用时单独验收。

## 交付前检查

- 运行 `git status --short`，只提交任务相关文件；保留用户未跟踪文件。
- Java 改动至少执行干净编译和相关测试。
- Python 改动执行 compileall、pytest 和 `uv lock --check`。
- 跨服务接口变更同步检查 Java 事件适配、Python schema、README 和设计文档。
- 任务/发布修改同步检查台账、代际、仲裁、心跳 Mapper XML、迁移 SQL、活动指针解析及迟到回调；不得把心跳期限当成恢复授权。
- 前端流式或预览改动同步检查累计字符数、2000 字符上限、80ms 刷新、停止/失败/卸载、聊天记录回源和单次预览刷新。
- 后端与前端是独立 Git 仓库，分别运行 `git status --short`，分别提交；不得把前端既有 `package-lock.json` 修改混入无关提交。
- 配置改动检查真实密钥、个人路径和生产地址。
- 代码生成链路改动检查三种生成类型、事件顺序、修复上限、取消和工具调用。
- 使用 `git diff --check` 检查空白错误。
- 仅文档修改时核对路径、命令、配置默认值与源码事实，无需仅为文档重跑真实模型或真实资源测试；明确记录未执行的运行验证。
- 不声称 Docker、真实模型或端到端流程通过，除非本轮确实执行并记录结果。

## 相关文档

- `ai-service/README.md`：AI 服务完整中文使用说明。
- `doc/ai-service-startup.md`：AI 服务详细启动和联调说明。
- `doc/ai-service-langchain-langgraph-refactor-design.md`：重构方案设计。
- `doc/ai-service-phase-one-handoff.md`：第一阶段重构交接说明。
- `doc/generation-task-ledger.md`：生成任务台账、代际、持久化仲裁、执行心跳、升级及回滚边界。
- `doc/ai-service-subgraph-refactor-handoff.md`：当前子图架构、恢复测试与真实验收边界。
- `doc/knowledge/product/README.md`：产品知识库与评估说明。
- `docs/superpowers/specs/2026-09-21-bounded-streaming-simple-prompts-design.md`：有限流式窗口与简短优化提示设计。
- `docs/superpowers/specs/2026-09-21-circular-preview-spinner-design.md`：预览加载图正圆修复设计。

## 给后续 Agent 的建议

- 先使用 `rg` 搜索实际调用链和配置，再改代码。
- 先区分当前实现、设计目标和历史实验代码。
- 小范围修改优先，不删除或恢复用户未明确要求的文件。
- 遇到外部依赖失败时记录证据，不假装验证通过。
- 跨 Java/Python 修改时列清接口、配置、测试和回滚影响。
- 保持中文业务文档和注释风格；Java 包名、Python 模块名和配置键使用既有英文命名。
