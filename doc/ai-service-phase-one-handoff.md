# AI 服务重构与第二阶段交接说明

## 1. 文档用途与当前基线

本文供下一轮 AI Agent 或开发者继续维护代码生成链路。内容更新至 2026-09-24 Vue 修复后最终源码审查实现基线，覆盖第一阶段 LangChain + LangGraph 重构，以及后续接入的 HTML 安全发布、生成取消治理、前端流式性能修复、Spring 工具响应契约加固、Python 提示词语义迁移、第二阶段 Task 1-4、稳定灰度、跨服务工具 Schema 校验和修复后 Vue 源码快照审查。

开始工作前依次阅读：

1. 根目录 `AGENTS.md`
2. `ai-service/README.md`
3. `doc/ai-service-startup.md`
4. `doc/ai-service-langchain-langgraph-refactor-design.md`
5. 本文

后端仓库为 `D:/VibeForge/yu-ai-code-mother`。配套 Vue 前端是独立仓库，默认位于 `D:/VibeForge/yu-ai-code-mother-frontend`。两个仓库需要分别检查状态、验证和提交。

## 2. 已实现范围

### AI 服务与跨服务边界

- Python 3.12 FastAPI AI 服务使用 LangChain、LangGraph 和 OpenAI 兼容模型适配器。
- LangGraph 支持 HTML、MULTI_FILE、VUE_PROJECT 三类分支、质量检查、最多两次修复、Vue 工具循环上限和协作式取消。
- Legacy Java 的路由、HTML、MULTI_FILE、Vue、质量检查语义已迁移到 `ai-service/src/ai_service/prompts/`，并由 `OpenAICompatibleModel` 实际加载；修复提示词按静态产物和 Vue 工具协议区分行为。
- Spring 通过 `AiGenerationGateway` 统一接入 Legacy、LangGraph 和灰度路由，并保持原有外部 SSE 协议。
- Python 不连接业务 MySQL，也不直接访问生成项目目录；文件、校验、发布和构建操作通过 Spring 内部工具网关完成。
- Java/Python 共享 `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json`，以 JSON Schema Draft 2020-12 统一十个内部工具的名称、历史别名、模型调用权限、请求参数和成功响应。
- Vue 模型工具已收敛为五个标准文件工具。Python 在出站前按 `requestSchema` 严格校验完整参数，未知工具、未知参数和额外字段不会到达 Spring；成功 `data` 按对应 `responseSchema` 校验后才进入工作流。请求拒绝额外字段，响应允许新增字段以支持滚动升级。
- Schema 错误使用稳定的脱敏信息，不包含源码、文件路径、参数值或响应正文。Spring 生产代码继续负责应用范围、路径安全、字段语义、权限等业务校验；Java JSON Schema 校验器和共享契约断言仅位于测试范围，不进入生产请求链路。
- 工作流在模型生成前调用仅工作流可用的 `artifact_context`：HTML/MULTI_FILE 返回完整活动产物且上限为 100000 字符，Vue 返回排序后的有界文件清单且最多 200 项；读取失败直接进入失败终态，不伪装成首次生成。
- Vue 首次生成和修复共用同一套受限工具循环，生成与修复共享 `AI_SERVICE_VUE_MAX_TOOL_CALLS` 总预算；工具调用 ID 分别使用 `vue-generate` 和 `vue-repair:<repairCount>` 前缀，非法工具、受控参数、取消和模型截断均在调用 Spring 前被阻止。
- Vue `project_build` 已返回 `built/errorCode/message` 结构化结果；`built=false` 会进入最多两次修复并重新校验、重新构建，达到上限后只能失败，质量检查只在构建成功后执行。
- Vue 至少完成一次修复、重新通过硬校验并重新构建成功后，质量检查通过 Spring 工作流专用且模型不可调用的 `vue_source_snapshot` 获取最终源码；首次未修复 Vue、HTML 和 MULTI_FILE 不调用。快照最多 24 个文件、单文件 12000 字符、总计 60000 字符；项目总访问条目（根目录之外的目录、文件和访问失败条目）最多 20000 个，其中合格源码候选最多 10000 个，只读取按优先级和路径稳定排序后的最佳 24 个，每个源文件最大 1 MiB。
- 快照排除依赖/构建产物、隐藏目录、符号链接、锁文件和非文本扩展名，依赖/构建目录及锁文件的大小写变体同样排除，并严格拒绝非法 UTF-8 或 NUL。完整内容仅瞬时传给当前 Reviewer，不进入 Spring 工具幂等 Redis、业务 checkpoint、LangGraph state/checkpoint 或事件；失败时可能产生的 pending checkpoint 也只保留稳定外层异常，不含源码。事件只公开 `eligibleFileCount`、`includedFileCount`、`omittedFileCount`、`truncated` 四个字段。读取失败使用稳定脱敏消息，错误响应不含绝对项目路径；读取或 Reviewer 失败不回退旧 artifact，而是进入失败终态。
- 内部 `project_build` 强制执行本轮构建，不以旧 `dist` 或上一轮并发构建结果冒充成功；旧预览仍保留。npm 输出、路径和环境值经过有界脱敏，进程树和输出读取使用有界终止策略。
- Redis checkpoint 默认使用数据库 2；不可用时按当前配置和实现降级。
- Spring 内部工具幂等状态和成功结果使用现有 Spring Redis 配置（本地默认 database 1），可跨 Spring 实例共享。作用域为 `appId + requestId + toolCallId`；同一作用域的 canonical 工具名或参数指纹不一致时拒绝执行。
- 已存在（包括陈旧）的 `RUNNING` 工具记录视为不确定状态，不自动重放。action 成功但完成状态写回 Redis 失败也属于 indeterminate，因此不承诺文件系统与 Redis 之间严格 exactly-once。
- Python `SpringToolGateway` 已识别 Spring HTTP 200 中非零 `BaseResponse.code`，仅向工作流保留四个 Redis 幂等稳定错误码，并对其他业务消息和协议错误做脱敏。非法 JSON、错误字段类型、缺少结果以及非精确旧版 `{"data": {...}}` envelope 都会被拒绝；明确业务错误不参与发布重试。

### 产物安全发布

- HTML 只接受一个完整闭合的 HTML 文档或唯一 HTML Markdown 代码块，拒绝前后解释、多代码块和截断内容。
- HTML 在发布前执行文档、CSS、JavaScript 完整性校验，并用 Selenium 检查脚本错误、永久加载态、外部图片失败和越界导航。
- MULTI_FILE 继续要求完整匹配的 `index.html`、`style.css` 和 `script.js`。
- HTML 和 MULTI_FILE 通过 `VersionedArtifactStore` 发布不可变版本，使用 manifest 哈希、请求墓碑、单调序号和原子活动指针。
- `VersionedArtifactStore` 的版本发布幂等与内部工具 Redis 幂等是独立机制，分别约束发布状态和工具调用，不能相互替代。
- 发布失败不会替换上一版本；默认保留当前版本和最近两个历史版本。
- Legacy HTML 和 LangGraph HTML/MULTI_FILE 都必须在 Spring 发布成功后才能产生完成终态。
- `MODEL_OUTPUT_TRUNCATED`、`HTML_FORMAT_INVALID`、`HTML_VALIDATION_FAILED` 和 `HTML_SMOKE_TEST_FAILED` 会作为结构化业务错误返回前端。
- `HtmlOutputBudgetGuard` 会拒绝对过大活动 HTML 进行不安全的整页重写。

### 并发、取消与终态

- `GenerationLeaseService` 使用 Redis 应用级锁阻止同一应用并发生成。
- 取消和提交使用 `ACTIVE`、`CANCELLED`、`COMMITTING`、`COMMITTED` 状态门仲裁；取消获胜后禁止发布，提交开始后不会再被迟到取消反向覆盖。
- 前端停止生成会关闭流并清理本地刷新任务；失败、取消和组件卸载后拒绝迟到回调。
- Python 在产物已经发布但外围 checkpoint 失败时最多补发一次完成事件，避免“文件已发布但前端收到失败”的矛盾终态。

### 前端生成体验

- SSE 保持流式展示，但完整源码不再逐分片写入 Vue 响应式状态。
- `generationStreamProgress` 在普通 JavaScript 状态中累计总字符数，只保留最近 2000 个字符，每 80ms 最多刷新一次界面。
- 临时 AI 消息显示累计字符数、耗时和最近文本；正常完成后重新加载服务端聊天历史作为完整最终消息。
- 自动滚动最多每 200ms 执行一次；用户离开底部后停止自动跟随。
- 生成期间保留上一版预览，只在当前请求发布成功后刷新一次；失败或取消不刷新。
- “优化提示”按 HTML、MULTI_FILE 和 VUE_PROJECT 返回简短普通语言，同时要求保留原有功能、文字、图片和操作方式，禁止无关内容、解释和残缺结果。
- 预览更新提示的 30px 双环加载图禁止 Flex 收缩并固定 1:1 比例，长文案下保持正圆。

## 3. 当前架构

```text
Vue EventSource
  -> GET /api/apps/chat/gen/code
  -> AppController
  -> AppServiceImpl
  -> GenerationLeaseService
  -> DelegatingAiGenerationGateway
     -> LegacyAiGenerationGateway -> LangChain4j
     或
     -> LangGraphAiGenerationGateway
        -> POST Python /internal/v1/generations:stream
        -> LangGraph StateGraph
        -> OpenAICompatibleModel / DeepSeek
        -> SpringToolGateway
        -> POST Spring /api/internal/ai-tools/invoke
        -> ArtifactPublicationService
        -> VersionedArtifactStore
  -> StreamHandlerExecutor
  -> 前端 SSE
```

Spring 是业务数据、项目文件和活动发布版本的唯一所有者。Python 负责模型与编排，不得绕过 Spring 写业务文件。

## 4. 关键文件索引

### Spring Boot

| 文件 | 作用 |
| --- | --- |
| `ai/gateway/AiGenerationGateway.java` | 统一生成契约 |
| `ai/gateway/DelegatingAiGenerationGateway.java` | Legacy、LangGraph 和灰度路由 |
| `ai/gateway/LangGraphAiGenerationGateway.java` | Python NDJSON 事件适配 |
| `ai/gateway/GenerationLeaseService.java` | 应用级租约与取消/提交仲裁 |
| `ai/gateway/ToolInvocationIdempotencyService.java` | Redis 工具幂等作用域、冲突检测与不确定态保护 |
| `controller/InternalAiToolsController.java` | 内部文件、校验、发布和构建工具边界 |
| `core/artifact/ArtifactContextReader.java` | 读取有界活动产物或 Vue 文件清单 |
| `core/artifact/VueSourceSnapshotReader.java` | 瞬时读取修复后 Vue 最终源码的有界快照 |
| `core/artifact/HtmlArtifactParser.java` | 严格解析单文件 HTML |
| `core/artifact/HtmlArtifactValidator.java` | HTML/CSS/JavaScript 确定性校验 |
| `core/artifact/SeleniumHtmlSmokeTester.java` | HTML 浏览器烟测 |
| `core/artifact/ArtifactPublicationService.java` | 解析、校验、烟测和发布编排 |
| `core/artifact/VersionedArtifactStore.java` | 不可变 release、manifest、墓碑和活动指针 |
| `core/artifact/HtmlOutputBudgetGuard.java` | 大型 HTML 整页重写预算保护 |
| `core/builder/VueBuildResult.java` | Vue 构建成功或稳定失败码的结构化结果 |
| `core/builder/VueProjectBuilder.java` | 强制/复用构建、npm 进程治理、输出脱敏与有界错误 |
| `service/impl/AppServiceImpl.java` | 生成入口、租约、历史和 SSE 生命周期 |

### Python AI 服务

| 文件 | 作用 |
| --- | --- |
| `ai-service/src/ai_service/app.py` | FastAPI 应用工厂和生命周期 |
| `api/routes.py` | 健康、路由、流式生成和取消接口 |
| `api/schemas.py` | 内部请求、响应和事件模型 |
| `orchestration/workflow.py` | 活动上下文、生成/修复工具循环、构建条件边和终态处理 |
| `orchestration/events.py` | 事件序号和异步队列 |
| `orchestration/cancellation.py` | Python 进程内协作式取消状态 |
| `models/openai_compatible.py` | DeepSeek/OpenAI 兼容模型适配及 Vue JSON 工具轮次解析 |
| `models/tool_contract.py` | Vue 模型工具提示与出站校验 |
| `prompts/routing.py` | 三类生成类型路由提示词 |
| `prompts/generation.py` | HTML、MULTI_FILE、Vue 生成及活动产物上下文规则 |
| `prompts/review.py` | 质量检查和修复提示词 |
| `contracts/internal-ai-tools-v1.json` | Java/Python 共享的版本化工具契约 |
| `infrastructure/checkpoint.py` | Redis checkpoint 和 LangGraph saver |
| `infrastructure/spring_tools.py` | Spring 工具网关客户端、发布重试和构建专用长读取超时 |

### Vue 前端

| 文件 | 作用 |
| --- | --- |
| `src/pages/AppChatView.vue` | 生成对话、停止、预览和终态回源 |
| `src/api/app.ts` | SSE 和业务错误事件适配 |
| `src/utils/generationStreamProgress.ts` | 80ms 刷新与 2000 字符尾部窗口 |
| `src/utils/previewRefreshCoordinator.ts` | 当前成功请求只刷新一次预览 |
| `src/utils/optimizePrompt.ts` | 三类简短优化提示和图片保护 |
| `tests/*.test.ts` | 流式进度、预览刷新和提示词测试 |

## 5. 当前 LangGraph 工作流

```text
START
  -> input_guard
  -> context_prepare -> Spring artifact_context
  -> HTML: generate_html
     MULTI_FILE: generate_multi_file
     VUE_PROJECT: vue_agent <-> Spring file tools
  -> artifact_validation
     HTML: 严格解析和确定性校验
     MULTI_FILE: 严格解析和确定性校验
     VUE_PROJECT: 非空产物校验
  -> VUE_PROJECT: project_build
       built=true  -> quality_review
       built=false -> repair 或 fail
     HTML/MULTI_FILE -> quality_review
  -> repair（校验、构建或质量不通过时最多 2 次）
       -> artifact_validation -> 必要时重新 project_build
  -> HTML: artifact_publish（staging Selenium 烟测通过后发布）
     MULTI_FILE: artifact_publish
     VUE_PROJECT: finalize
  -> finalize
  -> END
```

内部事件仍为 `content_delta`、`tool_started`、`tool_finished`、`node_status`、`completed`、`failed`。Java 对外继续维持现有 SSE 数据格式。

## 6. 配置与本地启动

三个令牌值必须一致：

```text
Python AI_SERVICE_INTERNAL_BEARER_TOKEN
= Python AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN
= Spring AI_SERVICE_INTERNAL_BEARER_TOKEN
```

它们是无自动过期能力的静态共享密钥，不得提交到仓库。

默认地址：

```text
Spring: http://localhost:8123/api
Python: http://localhost:8000
Vue: http://localhost:5173
Spring Redis: redis://localhost:6379/1
Python checkpoint Redis: redis://localhost:6379/2
```

用户当前偏好从 IDE 启动 Python AI 服务。自动化修改和验证期间不要擅自启动或占用 8000 端口；需要真实生成验收时先确认 IDE 中服务已启动。

## 7. 当前验证基线

2026-09-24 Vue 修复后最终源码审查分支的 fresh 离线验证结果：

- `cd ai-service && uv run python -m compileall -q src`：通过。
- `cd ai-service && uv run pytest`：165 项通过，0 failures；有 2 条来自 Starlette 和 LangGraph checkpoint 的第三方弃用警告。
- `cd ai-service && uv lock --check`：通过，锁文件解析 67 个包。
- `mvn "-Dtest=InternalAiToolContractTest,VueSourceSnapshotReaderTest,InternalAiToolsControllerTest,InternalAiToolsHttpContractTest" test`：共 54 项，0 failures、0 errors、1 skipped（Windows 符号链接权限条件测试按环境跳过），其余 53 项通过；其中 `VueSourceSnapshotReaderTest` 共 21 项、1 skipped。日志包含既有 SLF4J 多 provider、Mockito 动态 agent、Bean Validation provider 缺失提示及预期业务异常日志。
- `mvn clean -DskipTests compile`：通过，编译 237 个生产源文件；日志仅有既有 varargs、弃用 API 和 unchecked 操作警告，本轮只据此声明生产源码干净编译通过。

以上验证覆盖快照选择与限额、严格文本读取、Spring HTTP/Controller 边界、共享 Schema、Python 网关校验、修复后快照审查、事件脱敏、失败不回退及 checkpoint/state 不留存源码。Java 的 JSON Schema 依赖仍为 `test` scope，生产路径继续执行原有业务校验。

本分支全量 `mvn test` 新鲜证据为 188 项：0 failures、1 error、1 skipped，186 项通过。唯一 error 仍是既有的 `YuAiCodeMotherApplicationTests.contextLoads`，原因是测试上下文缺少 `openAiChatModel` bean；1 个 skipped 是 Windows 符号链接权限条件测试。本轮没有修复或重新声明全量测试通过，只声称上述定向测试和 `mvn clean -DskipTests compile` 通过。

此前 2026-09-22 本地 `dev` 基线还记录了以下结果：

- 后端定向测试通过：`CodeParserTest`、`HtmlArtifactValidatorTest`、`ArtifactPublicationServiceTest`、`SeleniumHtmlSmokeTesterTest`、`AiCodeGeneratorFacadeTest`、`InternalAiToolsControllerTest`、`AppServiceGenerationCancellationTest`、`AppControllerSseTest`。
- Redis 工具幂等本轮定向 Java 测试通过：`InternalAiToolsControllerTest`、`InternalAiToolContractTest`、`ToolInvocationIdempotencyServiceTest` 共 36 项，0 failures/errors/skips。
- 活动产物上下文阶段的 Java 相关测试共 28 项通过，覆盖 `ArtifactContextReader`、内部工具控制器和跨语言工具契约。
- Task 4 最新 Java 定向测试通过：`VueProjectBuilderTest` 与 `InternalAiToolsControllerTest` 共 33 项，覆盖结构化构建错误、4000 字符消息上限、Windows 路径变体脱敏、并发强制重建、8000 字符命令输出边界，以及真实父子进程持管道后的有界清理。
- Python 本轮 `compileall`、完整 `pytest` 和 `uv lock --check` 通过，其中 `pytest` 共 98 项测试通过并有 2 个既有依赖弃用警告。测试覆盖提示词加载、活动产物注入、Vue 生成/修复共享工具预算、非法工具和受控参数、取消、截断/过滤、构建失败修复上限、严格 `built` 类型及构建专用 HTTP 超时。多数测试仍使用 Fake Model、内存工具网关或 MockTransport，不属于真实 Spring 或端到端验证。
- 工作树本地存在未跟踪的 `InternalAiToolsHttpContractTest`，使用 standalone MockMvc 经过 `InternalAiToolsController`、JSON 绑定和 `GlobalExceptionHandler`，覆盖成功响应、Bearer 鉴权失败、缺少 `appId` 参数错误和稳定幂等错误消息；该文件尚未进入提交，且不加载 MyBatis、数据库或真实服务。
- `ToolInvocationIdempotencyRedisIT` 默认跳过；设置 `AI_REDIS_INTEGRATION=true` 后使用独立 Redisson 客户端验证跨客户端成功结果回放、陈旧 `RUNNING` 拒绝，以及 action 已成功但 Redis 连接在 `SUCCEEDED` 写回前丢失时保持不确定状态且不重复执行。未设置时不会连接或启动 Redis。
- 本次尝试执行真实 Redis 集成测试：`AI_REDIS_INTEGRATION=true mvn -Dtest=ToolInvocationIdempotencyRedisIT test`；Redisson 连接 `redis://127.0.0.1:6379/1` 时收到 `Connection refused`，因此未执行任何幂等断言。当前工作区未发现 6379、6380 或 16379 的监听端口，需确认 Redis 实际监听地址、端口及认证配置后重试。
- `mvn clean -DskipTests compile` 通过，Java 21 干净编译 236 个源文件；仓库当前没有 `mvnw.cmd`，本轮使用系统 `mvn`。
- 前端 `optimizePrompt`、`generationStreamProgress`、`previewRefreshCoordinator` 共 14 项测试通过。
- 前端 `npm run type-check` 和 `npm run build-only` 通过；构建仍有既有的大 chunk 警告。
- 浏览器确认简短优化提示包含图片保护要求，冗余生成提示已移除，加载图编译样式为固定 1:1 比例。
- 事故应用旧产物被确认含自然语言前缀并缺少 `</script>`、`</html>`；未对该产物执行覆盖或新生成。

本次最终源码审查阶段只完成离线验证，没有启动真实 Spring/Python HTTP、MySQL、Redis、多 Spring 实例或真实模型，也没有执行前端 HTML、MULTI_FILE、VUE_PROJECT 首次生成和二次修改。真实 Redis 恢复、跨实例共享、锁竞争和 Redis/文件系统故障窗口仍需在集成环境覆盖，不能据此声称上述真实环境验收已经通过。

### 稳定灰度与验收门实施记录

稳定灰度阶段使用 `graySalt + 稳定业务主体`（`userId` -> `appId` -> `requestId`）计算 SHA-256 灰度桶，并让 `route`、`generate`、`cancel` 复用同一引擎选择。仓库提供双引擎摘要对比、Spring/Python HTTP 和三类型端到端验收脚本；脚本默认 dry-run，必须传入隔离测试应用 ID 和 `-Execute` 才会发送真实请求。

稳定灰度阶段的实施与离线验证当时未启动外部服务，也未提高灰度比例。

### 会话关闭现场

- 当前后端分支为本地 `dev`，内部工具 JSON Schema 功能已从 `7bcb07f docs: 澄清工具 Schema 与交接边界` 基线继续推进 HTTP、Redis 和三类型验收入口。
- 2026-09-23 本轮优化提交前，本地 `dev` 与 `github/dev` 均指向 `7bcb07f`；本轮提交后会产生尚未推送的本地提交。后续必须以实时 `git status --short --branch` 和 `git log -1` 为准，未经用户确认不得推送。
- 2026-09-23 主工作树状态快照包含用户未提交内容：`.gitignore`、`ai-service/src/ai_service/orchestration/workflow.py` 注释、本文档既有编辑，以及未跟踪的 `docs/superpowers/plans/2026-09-22-spring-python-http-contract-tests.md` 和 `projects/`。后续必须以实时 `git status --short` 为准；这些内容均不得覆盖、回滚或混入无关提交。
- `ai-service/.env` 仅在本地存在，其中只补充过中文注释，真实配置值未改动且不得提交。

### 前端真实验收责任

- HTML、MULTI_FILE、VUE_PROJECT 的首次生成和二次修改，以及生成中停止、浏览器刷新/断线、旧预览保留、长时间 Vue 构建和最终预览刷新，由用户在完整项目启动后人工执行。
- 人工验收结果当前均视为“待验证”，不能因为前端可以正常发起请求就记录为通过。用户反馈具体错误后，再按错误所在边界修改 Java、Python 或前端，并补充对应回归测试。
- 本轮 Agent 继续推进不依赖前端页面的自动化事项，优先完成 Spring/Python 真实 HTTP 契约验收入口；真实模型生成、三类型产物质量和浏览器交互不由自动化离线测试代替。
- `scripts/test-ai-phase-two-e2e.ps1` 可以依次发起三种类型的首次生成和二次修改，并输出中文人工检查项及聊天、预览、下载、历史地址；脚本只能辅助收集终态，不能代替用户对页面视觉、功能、图片和交互的人工判断。

### 本轮已经完成的优化

1. **Python 提示词与工作流语义迁移。** 路由、HTML、MULTI_FILE、Vue、质量检查和修复提示词已由 Python 模型适配器加载；生成前会读取当前活动产物，Vue 首次生成和修复共用同一受限工具循环。
2. **Java/Python 职责边界落地。** Python 负责模型调用、工具决策、参数协议、调用预算和修复编排；Java 继续独占鉴权、应用范围、文件沙箱、真实读写、确定性校验、构建、发布、聊天记录和业务数据。
3. **工具幂等与生成终态加固。** Spring 已接入 Redis 工具幂等边界和生成租约；HTML/MULTI_FILE 只有在严格解析、校验和不可变版本发布成功后才能完成，Vue 构建失败会进入有限修复而不是误报完成。
4. **跨服务工具契约统一。** 十个内部工具已共享 Draft 2020-12 JSON Schema。Python 在请求发出前和成功响应返回后执行运行时校验；Java 测试使用同一 Schema 验证 Controller 请求样例和成功响应，生产业务校验不被 Schema 替代。
5. **稳定灰度路由实现。** `route`、`generate`、`cancel` 已统一使用 `graySalt + userId/appId/requestId` 的稳定业务桶，并提供双引擎摘要对比和真实环境验收脚本；当前尚未提高灰度比例。
6. **流式和预览体验治理。** 前端只保留最近 2000 字符、最多每 80ms 更新一次响应式快照，终态回源聊天历史；旧预览在生成期间保持，仅在当前请求成功后刷新一次。
7. **HTTP 验收入口已补齐。** `scripts/test-ai-service.ps1` 已增加 Python 健康检查、Spring 成功调用、缺少/错误令牌、缺少字段、构建可选开关、幂等错误和脱敏覆盖标签；默认 dry-run，只有显式 `-Execute` 才发送请求。真实 HTTP 仍待用户启动服务后执行。
8. **Redis 故障验收入口已补齐。** 真实 Redis 集成测试现已覆盖陈旧 `RUNNING` 和 action 成功后状态写回失败两个不确定窗口；重试必须拒绝再次执行 action。测试默认跳过，只有显式设置 `AI_REDIS_INTEGRATION=true` 才连接 `AI_REDIS_URL`。
9. **三类型人工验收入口已加固。** 三个应用 ID 必须为正数且互不相同，成功场景出现 `business-error` 或终态数量异常时立即失败；六个提示词、人工检查项和操作提示均使用中文。脚本默认 dry-run，不保存流式源码、账号、密码或 Cookie。
10. **离线验证完成。** 本轮 Python 165 项测试全部通过；Java 快照、契约、Controller 和 HTTP 定向测试共 54 项，0 failures/errors、1 项按 Windows 符号链接权限条件跳过；Python 编译、锁文件检查和 Java clean compile（237 个生产源文件）均通过。本分支全量 `mvn test` 共 188 项，其中 186 项通过、0 failures、1 error、1 skipped；唯一 error 是既有 `contextLoads` 因缺少 `openAiChatModel` bean，skipped 是 Windows 符号链接权限条件测试，因此本轮不声称全量测试通过。真实 Redis、真实 Spring/Python HTTP、真实模型和前端三类型首次生成/二次修改仍属于后续验收。
11. **Checkpoint 产物留存边界已收敛。** 业务快照不再保存完整 artifact，保留 `node`、`requestId`、`appId`、`codeGenType`、`qualityPassed`、`repairCount` 和 `toolCallCount` 等状态与审计摘要；执行期节点恢复由 LangGraph 自动 checkpoint 承担，该 checkpoint 在请求执行期间仍可能保留完整产物，成功、失败或取消进入终态后清理对应 thread，并使用 Redis 固定前缀避免误删。终态清理失败只使 checkpoint 就绪状态降级，不反转生成结果；TTL 作为异常退出兜底。本轮未执行真实 Redis、真实模型及 Spring/Python 真实服务依赖的自动化或接口验收；前端三类型生成、预览和交互由用户人工验证并反馈错误。
12. **Vue 修复后最终源码审查已完成。** 至少一次修复、硬校验和重新构建成功后，Reviewer 读取 Spring 瞬时生成的有界最终源码快照；扫描、选择、字符和文件大小均有硬上限，完整源码不进入幂等 Redis、业务或 LangGraph checkpoint/state/事件。读取或审查失败不回退旧 artifact，事件和异常保持脱敏。

## 8. 关键提交

本轮 Redis 工具幂等主实现与文档：

```text
0cc84a3 feat: 扩展内部工具请求作用域
5aa25f1 feat: 增加 Redis 工具幂等服务
f33f2e6 refactor: 接入 Redis 工具幂等边界
1cf8964 docs: 记录 Redis 工具幂等契约
```

后续加固提交：

```text
b397192 fix: 加固工具幂等作用域与类型语义
66ced8f fix: 保留幂等序列化基础配置
8f4b4e4 fix: 统一幂等结果回放类型
75bd652 fix: 校验内部工具应用作用域
597c300 fix: 保留 Spring 工具业务错误
972bd43 fix: 加固 Spring 工具响应边界
4231df0 fix: 收紧 Spring 工具错误白名单
069d1e9 docs: 明确 Spring 工具业务错误重试语义
d28d329 fix: 收紧旧版 Spring 响应兼容
```

内部工具 JSON Schema 合并基线：

```text
7bcb07f docs: 澄清工具 Schema 与交接边界
```

本轮内部工具 JSON Schema 已完成提交：

```text
a386ce9 docs: 设计内部工具 JSON Schema 契约
3d44a67 docs: 制定内部工具 Schema 实施计划
1d9c6ed feat: 定义内部工具 JSON Schema 契约
76459a2 feat: 校验 Spring 工具请求与响应
8125cae test: 校验 Java 内部工具共享契约
99bea78 test: 收紧内部工具 Schema 断言
b84fc73 docs: 记录内部工具 Schema 校验边界
74f1d68 docs: 修正 Schema 分支交接状态
7bcb07f docs: 澄清工具 Schema 与交接边界
```

Python 提示词迁移与第二阶段 Task 1-4：

```text
84d0236 docs: 设计 Python 提示词语义迁移
b0df889 docs: 制定 Python 提示词迁移计划
1ccf00f feat: 迁移 Python 代码生成提示词
4b14465 feat: 接入 Python 系统提示词
c6e3d7a docs: 制定 AI 服务第二阶段后续计划
16ea509 feat: 提供活动产物上下文工具
0e3f6f8 test: 覆盖活动产物上下文读取
9e2a431 feat: 注入当前活动产物上下文
f865858 refactor: 统一 Vue 生成与修复工具循环
fb52a41 fix: 阻止 Vue 构建失败进入完成终态
```

此前后端与文档基线：

```text
0ce5db7 merge: 接入 HTML 安全发布与流式性能治理
bb62018 docs: 记录运行分支接入验证结果
3bf392e docs: 设计有限流式窗口与简短优化提示
342c84a docs: 制定有限流式输出实施计划
f9c1ea9 docs: 设计预览加载图正圆修复
979dfbd docs: 制定加载图正圆修复计划
```

前端：

```text
cf45fdb merge: 接入生成流式性能与单次预览刷新
1bd3b07 fix: 优化时保留图片并精简生成提示
a380b55 feat: 显示有限长度的生成内容
a02ee1a refactor: 简化页面优化提示
69bb275 feat: 在生成进度中显示最新内容
caeb0cb fix: 保持预览加载图为正圆
```

## 9. 下一轮优化清单与原因

优先级含义：P0 是提高 LangGraph 灰度前的阻断项；P1 是真实链路通过后应处理的可靠性和成本问题；P2 是不阻断当前单实例灰度的生产化与架构演进。下一轮先执行 P0，不要直接进入多 Agent 或删除 Legacy。

### P0：真实环境验收门

1. **执行 Spring/Python 真实 HTTP 契约。** 验收入口已经实现，但当前 Java standalone MockMvc 和 Python MockTransport 仍只分别证明两端逻辑正确，无法覆盖真实端口、Bearer 配置、HTTP 超时、JSON 编解码和连接中断。需要用户启动真实 Spring 与 Python 后执行脚本，验证成功调用、非零业务码、鉴权失败、字段绑定失败、未知/额外字段、Schema 响应错误、构建长超时和错误脱敏。只有这一步通过，才能证明共享 Schema 在真实网络边界有效；该项不依赖前端人工生成。
2. **执行真实 Redis 工具幂等验收。** opt-in 集成测试已经覆盖跨客户端结果回放、陈旧 `RUNNING` 和 action 成功后写回失败窗口，但最近一次真实执行因 `127.0.0.1:6379` 拒绝连接而没有执行断言。需要在可连接 Redis 上运行，并继续核对相同 `toolCallId` 参数冲突和锁竞争。原因是网络重试即使在“一个账号对一个应用只有一个业务请求”的前提下仍可能重复提交写文件、删除或构建操作；但不追求跨 Redis 与文件系统的严格 exactly-once。
3. **完成三种生成类型的首次生成与二次修改（人工前端验收）。** 分别使用隔离的 HTML、MULTI_FILE、VUE_PROJECT 测试应用，验证聊天记录、完整内容、图片和交互保留、严格解析、HTML Selenium 烟测、Vue 工具循环、构建、不可变发布、活动指针和最终预览。原因是 Fake Model 无法证明真实模型会稳定遵守代码块、三文件和 JSON 工具协议，尤其无法证明二次修改不会丢失现有功能；该项等待用户执行并反馈错误。
4. **验证停止、断线和失败终态（人工前端 + 后台日志）。** 覆盖生成中停止、客户端断开、模型超时、Spring 工具失败、Vue 长构建和迟到回调，确认取消请求不能发布新版本，失败保留旧预览，前端只在当前请求成功后刷新一次。原因是这些行为横跨前端、Spring、Python 和 Redis，单服务测试无法证明终态一致；页面行为由用户人工观察，后端状态由日志和自动化脚本补证。
5. **验证稳定灰度和 Legacy 回滚。** 对相同业务主体重复执行 `route`、`generate`、`cancel`，确认始终选择同一引擎；使用摘要和哈希比较 Legacy/LangGraph 结果，并演练切回 Legacy。原因是稳定桶虽已实现，但提高灰度比例会扩大故障面，必须先证明路由一致、取消一致且回滚可用。灰度配置应作为独立提交修改。

### P1：可靠性、质量与资源优化

1. **瘦身 checkpoint 中的完整 artifact（已完成）。** 本次实际边界是：业务快照去除完整 artifact，保留 `node`、`requestId`、`appId`、`codeGenType`、`qualityPassed`、`repairCount` 和 `toolCallCount` 等状态与审计摘要；执行期节点恢复由 LangGraph 自动 checkpoint 承担，运行期 checkpoint 仍可保留完整产物，终态立即清理对应 thread，Redis 使用固定前缀防止误删，清理失败只降级 checkpoint 就绪状态、不反转终态。TTL 作为异常退出兜底；真实服务依赖的 Redis、模型和 Spring/Python 接口验收尚未执行，前端三类型生成、预览和交互由用户人工验证。
2. **让 Vue 修复后的质量检查读取修复后源码视图（已完成）。** 当前边界是：至少一次修复、硬校验和重新构建成功后才调用 `vue_source_snapshot`；最多 24 文件、单文件 12000 字符、总计 60000 字符，项目总访问条目最多 20000 个，其中合格源码候选最多 10000 个，并只读取排序最佳 24 个，每文件最大 1 MiB。依赖/构建产物、隐藏目录、符号链接、锁文件、非文本、非法 UTF-8 和 NUL 均被排除或拒绝，依赖/构建目录和锁文件按大小写不敏感处理。完整快照仅瞬时传给当前 Reviewer，不进入幂等 Redis、业务 checkpoint、LangGraph state/checkpoint 或事件；事件仅含四个统计字段，失败 pending checkpoint 只稳定外层异常。读取失败使用稳定脱敏消息且不暴露绝对项目路径；读取或 Reviewer 失败不回退旧 artifact；首次未修复 Vue、HTML、MULTI_FILE 不调用。
3. **明确应用创建阶段的灰度身份键（下一项 P1）。** 当路由发生在 appId 创建前，需要固定使用 userId，创建后再按既定优先级使用 appId，并增加跨阶段一致性测试。原因是身份键切换如果没有明确契约，同一次创建流程可能在 Legacy 和 LangGraph 之间漂移。若现有调用链证明创建阶段始终有稳定 appId，则记录证据后关闭此项，不为假设增加代码。
4. **压测 Java HTTP 客户端与长构建。** 验证 JDK HttpClient 连接复用、虚拟线程、读写超时、大 NDJSON 流、客户端断开和长时间 npm 构建下的资源释放。原因是功能测试覆盖正确性，但无法暴露连接耗尽、线程/进程残留、背压和超时边界问题。
5. **按证据决定是否升级构建进程治理。** 当前 npm 后代进程通过 100ms 轮询捕获并有界清理；只有压力测试复现漏进程时，才引入 Windows Job Object 或 Unix process group。原因是现有方案存在理论窗口，但直接引入平台相关进程管理会提高复杂度，应由可复现问题驱动。

### P2：生产化与后续架构演进

1. **拆分双向服务令牌并支持轮换。** 当前两个调用方向共用静态令牌，一处泄露会同时影响 Python 入口和 Spring 工具网关。应拆分权限域并设计新旧令牌并存的平滑轮换窗口。
2. **补齐可观测性。** 增加 requestId、appId、userId、engine、node、model、tool、status 的结构化日志，以及模型超时、重试、限流、token 使用、节点耗时和构建耗时指标。原因是灰度后必须能快速定位失败发生在哪一层，并量化 LangGraph 相对 Legacy 的质量、耗时和成本。
3. **接入 LangSmith 时保持旁路。** Trace、评估数据集和采样应默认隐藏完整提示词、源码和工具参数，LangSmith 故障不得改变业务终态。原因是可观测平台不能成为生成链路的新单点故障或源码泄露面。
4. **最后再引入 CloseAI 单模型多 Agent。** Planner、三类 Coder、Reviewer 和 Repairer 可以共享当前模型，但仍由确定性 LangGraph 控制预算、工具权限、校验、构建和发布。原因是多 Agent 会增加 token、延迟和状态复杂度；只有单工作流真实验收、指标和回滚能力稳定后才有评估收益的基础。
5. **灰度稳定后再删除 Legacy 和历史实验代码。** 原有 LangChain4j 目前仍是回滚路径，`langraph4j` 则是未接入主链路的实验目录。原因是过早删除 Legacy 会失去生产回退能力；删除前需要稳定期数据、回滚演练和引用扫描。
6. **检查并轮换历史凭据。** 对 Git 历史和部署配置中的 AI、OSS、邮件等密钥做检查和轮换。原因是删除工作区文件不能消除 Git 历史或外部日志中的泄露风险。

### 明确不进入下一轮的事项

- **不增加同一账号、同一应用的并发修改仲裁。** 当前业务约束保证同一账号对同一应用只存在一个生成请求，没有其他会话同时修改同一应用；在约束不变时继续优化该场景没有实际收益。
- **不实现跨进程共享取消。** Python 当前按单 worker、单实例部署，`CancellationRegistry` 的进程内状态满足现状。只有部署改为多 worker 或多实例时才重新评估。
- **不把文件、数据库、构建或发布迁移到 Python。** Python 继续负责 AI 决策，Spring 继续作为业务数据和项目文件的唯一所有者。
- **不在 P0 通过前提高灰度、删除 Legacy 或引入多 Agent。** 这些动作都会扩大故障面或削弱回滚能力。

## 10. 下一轮执行顺序

1. 用户启动 Spring/Python/Redis 后，先执行已实现的真实 Spring/Python HTTP 契约验收入口；Agent 根据失败报告修复问题。
2. 再连接真实 Redis，执行工具幂等集成测试并记录剩余故障窗口。
3. 使用三个隔离应用完成三类型首次生成和二次修改。
4. 执行停止、断线、大流式响应和长构建压力验证。
5. 对比 Legacy/LangGraph 摘要并演练回滚；全部通过后才能讨论提高灰度比例。
6. P1 的 checkpoint artifact 瘦身和 Vue 修复后最终源码审查已完成；下一项是明确应用创建阶段的灰度身份键，每项单独设计、测试和提交。
7. P2 只在真实运行数据证明有必要时进入实施，不与 P0/P1 混合提交。

## 11. 下一轮开始前检查清单

- [ ] 阅读本文和根目录 `AGENTS.md`。
- [ ] 分别检查后端和前端仓库的 `git status --short`。
- [ ] 实时确认本地 `dev` 与 `github/dev` 的 ahead/behind 状态；本轮会产生尚未推送的本地提交，后续未经用户确认不要自动推送。
- [ ] 保留 `.gitignore`、`workflow.py` 注释、本文档既有编辑、未跟踪 HTTP 契约计划和 `projects/` 的现有用户修改。
- [ ] 保留 `projects/`、本地 `.env`、前端既有 `package-lock.json` 等用户文件。
- [ ] 不自动启动 Python AI 服务；真实验收前确认用户是否已从 IDE 启动。
- [ ] 使用 `rg` 重新确认 Java、Python 和 Vue 实际调用链。
- [ ] 代码生成改动同时检查三种生成类型、发布终态、取消和错误码。
- [ ] 前端流式改动检查 2000 字符、80ms、停止、卸载、历史回源和单次预览刷新。
- [ ] Java 改动运行相关测试和干净编译；Python 改动运行 compileall、pytest、lock check；前端改动运行 Node 测试、类型检查和构建。
- [ ] 新增 Java、Python、PowerShell 和前端复杂逻辑时使用简洁中文注释说明边界、失败语义或非显然原因；不添加逐行复述代码的无效注释。
- [ ] 执行 `git diff --check`，只提交任务相关文件。
- [ ] 对未运行的真实模型、Docker 或端到端测试明确说明。

## 12. 不可擅自处理的内容

- `projects/` 下的用户生成文件。
- 用户本地 `.env`、`application-local.yml` 和其他凭据。
- 前端仓库中与当前任务无关的 `package-lock.json` 修改。
- 与 AI 链路无关的历史业务代码和乱码注释。
- 未经用户明确要求，不强制重置、删除分支、清理工作树或推送远端。

## 13. Python 提示词迁移与 AI/业务边界现状

### 13.1 当前提示词实现事实

Legacy Java 链路仍通过 LangChain4j 的 `@SystemMessage` 加载以下资源：

- `src/main/resources/prompt/codegen-routing-system-prompt.txt`
- `src/main/resources/prompt/codegen-html-system-prompt.txt`
- `src/main/resources/prompt/codegen-multi-file-system-prompt.txt`
- `src/main/resources/prompt/codegen-vue-project-system-prompt.txt`
- `src/main/resources/prompt/code-quality-check-system-prompt.txt`

Legacy 资源继续保留作为回滚链路。Python 已完成等价语义迁移，并由 `OpenAICompatibleModel` 实际加载：

- `prompts/routing.py`：固定路由输出为 `HTML`、`MULTI_FILE`、`VUE_PROJECT`。
- `prompts/generation.py`：分别约束 HTML 单代码块、MULTI_FILE 三文件协议、Vue 3 + Vite 工具调用，以及二次修改时的 `currentArtifact` 规则。
- `prompts/review.py`：区分模型质量判断和 Spring 确定性校验，并提供静态产物与 Vue 工具修复规则。

迁移是语义迁移而不是资源文件复制。Java 提示词仍服务 Legacy；Python 提示词服务 LangGraph，两条链路在稳定灰度和真实对比验收完成前并存。

### 13.2 已采用的迁移原则

本轮采用“语义迁移 + 工作流适配”，没有直接复制 Java 文本文件。当前 Python 提示词目录为：

```text
prompts/
  routing.py
  generation.py
  review.py
  __init__.py
```

对应关系如下：

| Legacy Java 提示词 | Python 使用位置 | 迁移要求 |
| --- | --- | --- |
| 路由提示词 | `model.route()` | 固定只返回 `HTML`、`MULTI_FILE`、`VUE_PROJECT` |
| HTML 生成提示词 | `model.generate("HTML")` | 保留完整 HTML、原生 CSS/JS、单代码块和修改边界约束 |
| MULTI_FILE 生成提示词 | `model.generate("MULTI_FILE")` | 保留三文件、顺序、文件名和完整输出约束 |
| Vue 生成提示词 | `model.generate("VUE_PROJECT")` | 重写为 Python 当前 JSON 工具调用协议，不直接照搬 Java 文案 |
| 质量检查提示词 | `model.review()` | 负责需求与功能质量判断，不替代 Java 确定性校验和构建 |
| 修复提示词 | `model.repair()` | 新增独立提示词，要求结合校验/构建错误返回完整可校验候选 |

二次修改所需上下文已经接入：`context_prepare` 在模型生成前调用 Spring `artifact_context`。HTML/MULTI_FILE 获得完整活动产物；Vue 获得有界文件清单，并继续通过 `file_read` 选择性读取源码。`artifact_context` 不是模型可调用工具，读取失败会终止工作流。

### 13.3 工具调用迁移目标

“尽量由 Python 负责 AI、由 Java 负责业务”是合理目标，但必须区分工具的**决策权**和**执行权**：

```text
Python AI 层：模型提示词、工具选择、参数校验、调用顺序、工具循环、修复决策
Java 业务层：鉴权、应用权限、租约、幂等、沙箱、文件系统、解析校验、构建、发布
```

当前 LangGraph 的 `VUE_PROJECT` 已按该边界运行：Python 根据模型响应决定 `dir_read`、`file_read`、`file_write`、`file_modify`、`file_delete`，首次生成和修复共用受限工具循环，再由 `SpringToolGateway` 调用 Java；Java 的 `InternalAiToolsController` 执行真实文件操作和项目构建。

推荐的目标链路是：

```text
模型（Python）
  -> Python 工具协议校验和工具循环
  -> SpringToolGateway
  -> Java InternalAiToolsController
  -> Java 文件/构建/发布能力
  -> 结果返回 Python
  -> 模型继续决策或进入下一工作流节点
```

### 13.4 可以迁移和不能迁移的部分

可以迁移到 Python 的部分：

- Legacy Java 中绑定模型工具的决策逻辑；
- Vue 项目工具调用的提示词和调用顺序；
- 工具参数的模型侧校验；
- 工具调用次数上限、循环退出条件和重试策略；
- 根据文件读取结果决定下一次模型动作；
- 根据校验/构建结果决定修复还是完成。

不应迁移到 Python 的部分：

- 文件真实读写和删除；
- 应用目录沙箱、路径穿越防护和受保护文件判断；
- `appId` 权限校验、用户身份和生成租约；
- Spring Redis 工具幂等及不确定状态保护；
- HTML/MULTI_FILE 严格解析和确定性校验；
- Selenium HTML 烟测；
- Vue `npm install`、`npm run build` 和构建产物判定；
- `VersionedArtifactStore` 的 release、manifest、墓碑和活动指针切换；
- 对外 SSE、聊天历史和业务数据库写入。

Python 不得获得项目目录挂载、数据库连接或绕过 Spring 的本地文件权限。否则会破坏当前“Spring 是业务数据和项目文件唯一所有者”的安全边界。

### 13.5 对 Legacy Java 工具链的迁移评估

Legacy Java 的 `AiCodeGeneratorService`、`ToolManager` 和 `BaseTool` 中仍保留 LangChain4j 工具调用链作为回滚路径。Python LangGraph 已接管新链路的模型工具决策，但没有把 Java 工具类翻译成 Python 文件工具。当前实现遵循：

1. 保留 Java 工具实现作为唯一执行端；
2. 将 Python 的 `internal-ai-tools-v1.json` 作为唯一工具名称和参数契约；
3. Python LangGraph 的 Vue 首次生成和修复复用同一受限工具循环；
4. 让 Python 通过 `SpringToolGateway` 调用 Java；
5. 在 Java 侧保留兼容别名和旧 Legacy 路径，直到 LangGraph 灰度稳定；
6. 后续验证新旧链路的文件结果、构建结果、聊天记录和取消语义一致后，再评估删除 Java 模型工具绑定。

该迁移不会减少 Java 工具执行代码的必要性，只会把“模型应该调用哪个工具、何时调用、调用几次”的控制权从 Java LangChain4j 移到 Python LangGraph。

### 13.6 完成情况和剩余验收

当前进度：

- [x] 迁移路由、HTML、MULTI_FILE、Vue、质量检查和修复提示词，并增加 Python 单元测试。
- [x] 在生成前注入有界活动产物上下文；上下文失败不得降级为首次生成。
- [x] 统一 Vue 首次生成与修复工具循环，保持 Spring 为唯一文件执行边界。
- [x] 将 `project_build` 失败纳入确定性修复状态机，并提供结构化、有界、脱敏的构建结果。
- [x] 在保留 Legacy 回滚路径的前提下，实现稳定用户灰度桶并提供双引擎摘要对比脚本。
- [x] 使用共享 JSON Schema 统一十个内部工具的请求/成功响应协议，并在 Python 运行时和 Java 测试期校验。
- [x] 在 Vue 至少修复一次、硬校验和重新构建成功后，通过 Spring 瞬时有界快照让 Reviewer 审查最终源码；失败不回退旧 artifact，源码不进入持久状态或事件。
- [ ] 在真实环境执行双引擎摘要对比和 Legacy 回滚演练。
- [ ] 完成真实 Spring HTTP、真实 Redis、多实例幂等、取消和三类型首次生成/二次修改验收后，再提高 LangGraph 灰度比例。

迁移完成的判定标准不是“Python 中出现了工具类”，而是：LangGraph 新链路的模型决策由 Python 控制；Java 仍是所有业务数据、文件、构建和发布操作的唯一可信执行边界；失败、取消、重试和幂等语义在 Legacy 与 LangGraph 两条链路中保持一致。
