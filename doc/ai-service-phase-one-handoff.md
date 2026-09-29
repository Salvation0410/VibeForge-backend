# AI 服务 LangGraph 交接说明

> 更新日期：2026-09-29
> 当前分支：`codex/langgraph-real-gate`
> 文档目标：让后续开发者用最短时间确认当前事实、验证证据、人工验收门和下一步优先级。

## 1. 五分钟接手摘要

### 当前状态

- Spring Boot 仍是业务数据、项目文件、构建与发布状态的唯一所有者。
- Python AI 服务负责模型调用、LangGraph 编排、工具选择、参数校验、质量检查和有限修复，不连接业务 MySQL，也不直接读写项目目录。
- Spring 通过 `AiGenerationGateway` 在 Legacy、LangGraph 和稳定灰度模式之间路由，对前端继续保持原有 SSE 协议。
- LangGraph 已支持 HTML、MULTI_FILE、VUE_PROJECT 三类生成、最多两次修复、Vue 有界工具循环、构建失败修复、质量检查、取消和终态事件。
- HTML 与 MULTI_FILE 只有在 Spring 严格解析、确定性校验和不可变版本发布成功后才能完成；HTML 还执行 Selenium 浏览器烟测。
- Legacy LangChain4j 仍是生产回滚路径。真实验收门通过前不得删除 Legacy、提高灰度比例或引入多 Agent。

### 当前最高优先级

1. 在真实 Spring、Python、Redis、模型和前端环境中完成三种生成类型的首次生成与二次修改。
2. 验证停止、断线、模型超时、工具失败和长构建时的取消、旧预览保留和唯一终态。
3. 完成两个真实 Spring 实例对同一工具调用的竞争与结果回放。
4. 完成 Legacy/LangGraph 摘要对比和切回 Legacy 的回滚演练。

### 已关闭的源码阻塞

- Java LangGraph 客户端已固定使用 HTTP/1.1，h2c Upgrade 导致的 HTTP 422 已有回归测试，不再是当前源码阻塞。
- 下游取消会取消未完成 HTTP future、关闭 NDJSON 响应体并终止读取循环。
- LangGraph 响应流支持可配置空闲超时，默认 600 秒；该值按连续未收到完整 NDJSON 行计时，不是整轮总时长。
- 灰度身份键已明确为 `userId -> appId -> requestId`，覆盖应用创建前后、生成和取消的一致性。
- 连接复用、并发大流隔离、超时风暴恢复和重复父子进程回收已有自动化覆盖。

### 接手前先读

1. `AGENTS.md`
2. `ai-service/README.md`
3. `doc/ai-service-startup.md`
4. `doc/ai-service-langchain-langgraph-refactor-design.md`
5. 本文

后端和前端是两个独立 Git 仓库：

- 后端：`D:/VibeForge/yu-ai-code-mother`
- 前端：`D:/VibeForge/yu-ai-code-mother-frontend`

必须分别检查工作区、运行验证和提交，不得把前端既有 `package-lock.json` 修改混入无关提交。

## 2. 系统边界与当前架构

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

边界原则：

- Python 决定模型提示词、工具选择、参数校验、调用顺序、修复和工作流流转。
- Spring 负责鉴权、应用权限、生成租约、幂等、目录沙箱、文件操作、解析、校验、构建、发布、聊天历史和数据库写入。
- Python 不获得项目目录挂载、业务数据库连接或绕过 Spring 的文件权限。
- 内部 Spring/Python 使用 NDJSON；对外 SSE 兼容性由 Java 网关维持。
- `VersionedArtifactStore` 的发布幂等与内部工具 Redis 幂等是两套独立机制，不可混为一谈。

## 3. 当前已实现能力

### 3.1 LangGraph 工作流

```text
START
  -> input_guard
  -> context_prepare -> Spring artifact_context
  -> HTML: generate_html
     MULTI_FILE: generate_multi_file
     VUE_PROJECT: vue_agent <-> Spring file tools
  -> artifact_validation
  -> VUE_PROJECT: project_build
  -> quality_review
  -> repair（校验、构建或质量失败时最多 2 次）
  -> HTML/MULTI_FILE: artifact_publish
     VUE_PROJECT: finalize
  -> END
```

- 内部事件为 `content_delta`、`tool_started`、`tool_finished`、`node_status`、`completed`、`failed`。
- HTML 只接受完整闭合文档或唯一 HTML Markdown 代码块，拒绝解释、多代码块和截断内容。
- MULTI_FILE 要求完整匹配的 `index.html`、`style.css`、`script.js`。
- Vue 首次生成与修复复用五个标准文件工具，并共享 `AI_SERVICE_VUE_MAX_TOOL_CALLS` 总预算。
- `project_build` 返回 `built/errorCode/message`；失败进入有限修复，质量检查只在构建成功后执行。
- 至少修复一次且重新构建成功的 Vue，会通过 `vue_source_snapshot` 让 Reviewer 瞬时审查最终源码。

### 3.2 工具契约与安全

- `ai-service/src/ai_service/contracts/internal-ai-tools-v1.json` 是 Java/Python 共享的版本化工具契约。
- Python 在出站前按请求 Schema 严格校验工具参数；未知工具、未知参数和额外字段不会到达 Spring。
- Spring 成功响应会按响应 Schema 校验；请求拒绝额外字段，响应允许新增字段以支持滚动升级。
- Schema、协议和业务错误使用稳定脱敏信息，不包含源码、绝对路径、参数值或响应正文。
- 内部工具幂等作用域为 `appId + requestId + toolCallId`；工具名或参数指纹冲突会拒绝执行。
- 已存在的 `RUNNING` 和 action 成功但 Redis 完成状态写回失败，都按不确定状态处理，不承诺文件系统与 Redis 严格 exactly-once。
- Python `SpringToolGateway` 会识别 HTTP 200 中非零的 Spring `BaseResponse.code`，不会把业务失败当作成功。

### 3.3 产物发布、并发与取消

- HTML 在发布前执行文档、CSS、JavaScript 完整性校验和 Selenium 烟测。
- HTML/MULTI_FILE 使用不可变 release、manifest、请求墓碑、单调序号和原子活动指针。
- 发布失败、模型截断或页面运行检查失败时，上一活动版本保持不变。
- `GenerationLeaseService` 防止同一应用并发生成。
- 取消和提交使用 `ACTIVE`、`CANCELLED`、`COMMITTING`、`COMMITTED` 状态门仲裁。
- 取消先获胜时禁止发布；提交已经开始后，迟到取消不能反向覆盖成功版本。
- Python 在产物已发布但外围 checkpoint 失败时最多补发一次完成事件，避免文件已发布而前端收到失败。

### 3.4 前端生成体验

- 前端只在普通 JavaScript 状态中累计完整流式进度，Vue 响应式状态仅保留最近 2000 个字符。
- 界面每 80ms 最多刷新一次，自动滚动每 200ms 最多一次。
- 完成后重新读取服务端聊天历史，不能把 2000 字符临时窗口当成最终完整消息。
- 生成期间保留旧预览，仅在当前请求成功后刷新一次；失败、停止、卸载和迟到回调不得刷新。
- 三类“优化提示”均使用简短普通语言，并要求保留原有功能、文字、图片和操作方式。

### 3.5 资源边界

- 活动 HTML/MULTI_FILE 上下文最多 100000 字符；Vue 上下文最多返回 200 个排序后的文件项。
- 修复后 Vue 快照最多 24 个文件、单文件 12000 字符、总计 60000 字符。
- Vue 项目总访问条目最多 20000 个，合格源码候选最多 10000 个，单文件最大 1 MiB。
- 快照排除依赖、构建产物、隐藏目录、符号链接、锁文件、非文本、非法 UTF-8 和 NUL。
- 完整快照只瞬时传给当前 Reviewer，不进入工具幂等 Redis、业务 checkpoint、LangGraph state/checkpoint 或事件。
- npm 输出、路径和环境值有界且脱敏；父子进程树和输出读取使用有界终止策略。

## 4. 关键文件

### Spring Boot

| 仓库相对路径 | 作用 |
| --- | --- |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/AiGenerationGateway.java` | 统一生成契约 |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/DelegatingAiGenerationGateway.java` | Legacy、LangGraph 和灰度路由 |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/LangGraphAiGenerationGateway.java` | Python NDJSON 事件适配、HTTP 生命周期与空闲超时 |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/GenerationLeaseService.java` | 应用级租约与取消/提交仲裁 |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/ToolInvocationIdempotencyService.java` | Redis 工具幂等和不确定态保护 |
| `src/main/java/com/yupi/yuaicodemother/controller/InternalAiToolsController.java` | 内部文件、校验、发布和构建工具边界 |
| `src/main/java/com/yupi/yuaicodemother/core/artifact/ArtifactPublicationService.java` | 解析、校验、烟测和发布编排 |
| `src/main/java/com/yupi/yuaicodemother/core/artifact/VersionedArtifactStore.java` | 不可变版本、墓碑和活动指针 |
| `src/main/java/com/yupi/yuaicodemother/core/artifact/VueSourceSnapshotReader.java` | 修复后 Vue 最终源码有界快照 |
| `src/main/java/com/yupi/yuaicodemother/core/builder/VueProjectBuilder.java` | 强制构建、进程治理和错误脱敏 |
| `src/main/java/com/yupi/yuaicodemother/service/impl/AppServiceImpl.java` | 生成入口、租约、历史和 SSE 生命周期 |

### Python AI 服务

| 仓库相对路径 | 作用 |
| --- | --- |
| `ai-service/src/ai_service/app.py` | FastAPI 应用工厂和依赖组装 |
| `ai-service/src/ai_service/api/routes.py` | 健康、流式生成和取消接口 |
| `ai-service/src/ai_service/api/schemas.py` | 内部请求、响应和事件模型 |
| `ai-service/src/ai_service/orchestration/workflow.py` | LangGraph 工作流、修复、构建和终态 |
| `ai-service/src/ai_service/orchestration/cancellation.py` | 单进程协作式取消 |
| `ai-service/src/ai_service/models/openai_compatible.py` | OpenAI 兼容模型与 Vue 工具轮次解析 |
| `ai-service/src/ai_service/models/tool_contract.py` | 模型工具提示和出站校验 |
| `ai-service/src/ai_service/prompts/` | 路由、生成、审查和修复提示词 |
| `ai-service/src/ai_service/infrastructure/checkpoint.py` | Redis checkpoint 和降级 |
| `ai-service/src/ai_service/infrastructure/spring_tools.py` | Spring 工具客户端与脱敏错误处理 |

### Vue 前端独立仓库

| 前端仓库相对路径 | 作用 |
| --- | --- |
| `src/pages/AppChatView.vue` | 生成对话、停止、预览和终态回源 |
| `src/api/app.ts` | SSE 与 `business-error` 事件适配 |
| `src/utils/generationStreamProgress.ts` | 80ms 刷新和 2000 字符尾部窗口 |
| `src/utils/previewRefreshCoordinator.ts` | 当前成功请求只刷新一次预览 |
| `src/utils/optimizePrompt.ts` | 三类简短优化提示 |

## 5. 配置与本地服务

默认地址：

```text
Spring: http://localhost:8123/api
Python: http://localhost:8000
Vue: http://localhost:5173
Spring Redis: redis://localhost:6379/1
Python checkpoint Redis: redis://localhost:6379/2
```

当前两个调用方向仍共用静态令牌，本地联调时以下值必须一致：

```text
Python AI_SERVICE_INTERNAL_BEARER_TOKEN
= Python AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN
= Spring AI_SERVICE_INTERNAL_BEARER_TOKEN
```

不得把真实令牌、模型密钥、账号、密码、Cookie、验证码、OSS 密钥或邮件授权码写入仓库、测试报告或交接文档。生产环境应通过密钥管理或运行时环境变量注入并定期轮换。

关键配置：

- `AI_ENGINE=legacy|langgraph|gray|auto`
- `AI_SERVICE_URL=http://localhost:8000`
- `AI_GENERATION_STREAM_IDLE_TIMEOUT_SECONDS=600`
- `AI_SERVICE_VUE_MAX_TOOL_CALLS=<有限正整数>`
- `AI_REDIS_INTEGRATION=true`：只在明确运行真实 Redis 集成测试时设置。

本地真实验收前先确认服务和配置，不要把“端口当前可达”“当前终端有令牌”等临时现场写成长期事实。

## 6. 当前自动化验证证据

下表只记录 2026-09-29 当前分支可复现的最新基线。历史测试数量不再作为当前结论。

| 范围 | 命令 | 最新证据 | 能证明什么 |
| --- | --- | --- | --- |
| Java 定向门禁 | `mvn "-Dtest=LangGraphAiGenerationGatewayTest,DelegatingAiGenerationGatewayTest,AppServiceGenerationCancellationTest,VueProjectBuilderTest" test` | 35 项通过 | HTTP 生命周期、灰度、取消、长构建治理 |
| Java 网关 | `mvn "-Dtest=LangGraphAiGenerationGatewayTest" test` | 10 项通过 | HTTP/1.1、连接复用、大流隔离、空闲超时与恢复 |
| Vue 构建器 | `mvn "-Dtest=VueProjectBuilderTest" test` | 16 项通过 | 构建错误边界、输出限制、父子进程回收 |
| Java 生产编译 | `mvn clean -DskipTests compile` | 239 个生产源文件编译成功 | 当前生产源码可干净编译 |
| Redis opt-in 集成 | `mvn "-Dtest=ToolInvocationIdempotencyRedisIT" "-DAI_REDIS_INTEGRATION=true" test` | 最近真实环境 6 项通过 | 跨客户端回放、唯一执行、冲突与不确定态 |
| PowerShell 脚本 | `powershell -NoProfile -File scripts/ai-validation-scripts.tests.ps1` | Windows PowerShell 5.1 与 PowerShell 7 检查通过 | 验收脚本参数、认证和脱敏约束 |

重要限制：

- 全量 `mvn test` 存在历史实验代码和外部依赖相关失败，当前不能声明全量 Java 测试通过。
- Python 大部分测试使用 Fake Model、内存网关或 MockTransport，不能替代真实模型、真实 Spring 和完整前端验收。
- 受控本地 HTTP/进程测试不能证明真实 Uvicorn、代理、供应商限流、网络背压或 npm 包装层在所有平台上的行为。
- 未实际运行的 Docker、真实模型、浏览器端到端和生产灰度，必须明确标记为未验证。

## 7. 当前人工与真实环境验收门

### 7.1 状态总表

| 优先级 | 验收项 | 当前状态 | 通过标准 |
| --- | --- | --- | --- |
| P0 | 三类型首次生成和二次修改 | 待执行 | 三个隔离应用均完成，历史、完整内容、图片、交互、构建、发布和预览正确 |
| P0 | 停止、断线和失败终态 | 待执行 | 取消不发布新版本，失败保留旧预览，迟到回调不刷新，终态唯一 |
| P0 | 双 Spring 工具竞争 | 自动化入口已具备，真实执行待完成 | 同一作用域只执行一次，另一实例回放同一成功结果，新作用域探针保持独立 |
| P0 | 双引擎摘要与 Legacy 回滚 | Legacy 单链路已有成功记录，完整演练待完成 | 同一主体路由稳定，可比较摘要，可切回 Legacy 且取消语义一致 |
| P1 | 真实 Uvicorn/代理压力 | 待执行 | 无连接泄漏，慢流/背压可控，超时后线程与连接收敛 |
| P1 | 真实 npm 长构建压力 | 待执行 | 取消/超时后无可复现残留 PID 或管道阻塞 |

### 7.2 三类型生成

为 HTML、MULTI_FILE、VUE_PROJECT 分别创建隔离测试应用，每类至少执行：

1. 首次生成完整应用。
2. 保留原功能、文字、图片和操作方式的二次修改。
3. 检查聊天历史回源内容完整，不以 2000 字符临时窗口代替最终消息。
4. 检查成功后预览只刷新一次，失败或取消不刷新。
5. 检查 HTML 严格解析与烟测、MULTI_FILE 三文件、Vue 工具循环与构建结果。
6. 检查新版本发布后活动指针更新，失败时旧活动版本仍可预览。

### 7.3 停止、断线和失败

覆盖以下场景并同时观察浏览器、Spring、Python 和 Redis 状态：

- 生成过程中点击停止。
- 客户端主动断开 SSE。
- 模型连接超时或响应流长期无完整 NDJSON 行。
- Spring 工具返回业务失败。
- Vue 构建长时间运行后取消或超时。
- 成功、失败或停止后的迟到回调。

报告只记录 requestId、appId、生成类型、引擎、终态、稳定错误码、耗时和是否保留旧预览。不得保存完整源码、提示词、工具参数、令牌、Cookie、账号密码或绝对临时路径。

### 7.4 双 Spring 工具竞争

使用 `scripts/test-ai-tool-controller-competition.ps1` 对两个 Spring 实例发起相同 `appId + requestId + toolCallId` 的并发调用。验收要求：

- 使用随机隔离临时文件，不触碰 `projects/` 中用户产物。
- 两个实例共享同一 Redis database 1。
- 首次竞争只有一个真实副作用，另一个实例得到相同成功结果或受控不确定态。
- 再次调用相同作用域回放结果；新 requestId/toolCallId 不得误命中旧结果。
- 报告脱敏，不记录 Bearer 令牌、Cookie、临时绝对路径或响应正文。

### 7.5 双引擎对比与回滚

使用 `scripts/compare-ai-generation-engines.ps1` 比较 Legacy/LangGraph 的摘要和稳定路由，不保存完整生成源码。提高灰度比例前必须证明：

- 相同 userId 在应用创建、持久化后生成和取消阶段选择同一引擎。
- Legacy 和 LangGraph 均可产生可发布结果或稳定失败终态。
- 切回 `legacy` 后生成、取消、聊天历史和旧预览语义正常。
- 灰度配置变更作为独立提交，不能和功能修复混在一起。

## 8. 未完成优化清单

### P0：先关闭真实验收门

1. 完成第 7 章四项真实环境验收并保存脱敏摘要。
2. 将人工发现的问题转成最小可复现测试，再修复源码；不要只改验收脚本绕过失败。
3. P0 全部通过后再评估提高灰度比例，任何提高都必须保留 Legacy 一键回滚。

### P1：提高验收可复现性与资源证据

1. 提供统一验证入口，固定执行 Java clean compile、35 项定向测试、PowerShell 静态检查，并可选运行真实 Redis 6 项；输出脱敏机器可读摘要。
2. 在真实 Uvicorn/代理环境观测连接复用、慢流、网络背压、空闲超时和虚拟线程收敛。
3. 对真实 npm/Shell 包装层执行重复长构建、取消与超时压力；只有复现残留进程时才引入 Windows Job Object 或 Unix process group。
4. 为 P0 人工验收建立统一记录模板，明确环境、场景、requestId、终态、错误码和旧预览状态。

### P2：真实运行稳定后再做

1. 拆分 Python 入口令牌和 Spring 工具网关令牌，并设计新旧令牌并存的轮换窗口。
2. 增加 requestId、appId、userId、engine、node、tool、status、节点耗时、模型耗时和构建耗时的结构化观测。
3. 如接入 LangSmith，保持旁路、采样和源码脱敏，观测故障不得改变业务终态。
4. 只有单工作流真实验收、指标和回滚稳定后，才评估 CloseAI 单模型多 Agent。
5. 灰度稳定期结束且完成回滚演练后，才评估删除 Legacy 和未接入主链路的 Java LangGraph4j 实验目录。
6. 检查 Git 历史和部署配置中的 AI、OSS、邮件等历史凭据并执行轮换。

### 明确不做

- 不把文件、数据库、构建或发布迁移到 Python。
- 当前单 worker、单实例 Python 部署下，不实现跨进程共享取消；扩容到多 worker/实例时重新评估。
- 业务约束未变化前，不额外实现同账号同应用的并发修改仲裁。
- P0 未通过前不提高灰度、不删除 Legacy、不引入多 Agent。

## 9. 最近关键提交

| 提交 | 内容 |
| --- | --- |
| `5eab7d9` | 打通 LangGraph 真实环境验收门基础能力 |
| `ffa72de` | 明确灰度身份键契约 |
| `f6e6f1a` | 取消时释放 LangGraph 响应流 |
| `cae015b` | 覆盖并发大流请求隔离 |
| `2a14a39` | 限制 LangGraph 空闲响应流 |
| `9358f18` | 增加双 Spring 工具竞争验收 |
| `012f93b` | 覆盖 HTTP 连接复用与超时恢复 |
| `2c91f21` | 压测长构建进程树回收 |

这些提交都在 `codex/langgraph-real-gate`。未经用户明确要求，不自动推送、合并、删除分支或清理工作树。

## 10. 接手检查与禁止事项

开始修改前：

- [ ] 阅读 `AGENTS.md`、本交接文档和相关模块 README/设计文档。
- [ ] 分别运行后端和前端仓库的 `git status --short --branch`。
- [ ] 使用 `rg` 确认实际调用链、配置键和测试，不把规划能力当成已实现能力。
- [ ] 确认真实验收需要的 Spring、Python、Redis、前端和模型环境由谁启动。
- [ ] 确认内部令牌只存在于本地环境变量或密钥配置，不进入命令回显和报告。

提交前：

- [ ] Java 改动运行相关测试和 `mvn clean -DskipTests compile`。
- [ ] Python 改动运行 `uv run python -m compileall -q src`、`uv run pytest`、`uv lock --check`。
- [ ] 前端改动运行三组 Node 测试、`npm run type-check`、`npm run build-only`。
- [ ] 跨服务变更同步检查 Java 事件适配、Python schema、共享契约、README 和本文。
- [ ] 运行 `git diff --check`，只提交任务相关文件。
- [ ] 对未执行的真实模型、Docker、端到端或全量测试明确说明。

禁止擅自处理：

- `projects/` 下的用户生成文件。
- `.env`、`application-local.yml`、真实密钥、账号、Cookie 和个人路径。
- 前端仓库中与当前任务无关的 `package-lock.json`。
- 与 AI 链路无关的历史代码、乱码注释或大范围重构。
- 未经用户要求，不强制重置、删除分支、清理工作树、推送远端或覆盖用户未提交修改。

## 11. 相关设计与历史记录

- `ai-service/README.md`：Python AI 服务使用、配置和测试说明。
- `doc/ai-service-startup.md`：本地启动与联调步骤。
- `doc/ai-service-langchain-langgraph-refactor-design.md`：重构架构与边界设计。
- `docs/superpowers/specs/2026-09-21-bounded-streaming-simple-prompts-design.md`：有限流式窗口和简短优化提示设计。
- `docs/superpowers/specs/2026-09-21-circular-preview-spinner-design.md`：预览加载图正圆修复设计。

需要追溯具体历史阶段时使用 Git 记录和上述设计文档。本文只维护当前有效事实、最新验证证据、尚未关闭的门禁和下一步顺序，不再累计逐轮日志。
