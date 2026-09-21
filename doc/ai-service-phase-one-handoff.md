# AI 服务第一阶段重构交接说明

## 1. 文档用途与当前基线

本文供下一轮 AI Agent 或开发者继续维护代码生成链路。内容已更新到 2026-09-21 的 `dev` 分支，覆盖第一阶段 LangChain + LangGraph 重构，以及后续接入的 HTML 安全发布、生成取消治理和前端流式性能修复。

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
- Spring 通过 `AiGenerationGateway` 统一接入 Legacy、LangGraph 和灰度路由，并保持原有外部 SSE 协议。
- Python 不连接业务 MySQL，也不直接访问生成项目目录；文件、校验、发布和构建操作通过 Spring 内部工具网关完成。
- Vue 模型工具已收敛为五个标准文件工具；Python 在出站前校验名称和参数，Java/Python 通过包内版本化 JSON 契约防止名称漂移。
- Redis checkpoint 默认使用数据库 2；不可用时按当前配置和实现降级。

### 产物安全发布

- HTML 只接受一个完整闭合的 HTML 文档或唯一 HTML Markdown 代码块，拒绝前后解释、多代码块和截断内容。
- HTML 在发布前执行文档、CSS、JavaScript 完整性校验，并用 Selenium 检查脚本错误、永久加载态、外部图片失败和越界导航。
- MULTI_FILE 继续要求完整匹配的 `index.html`、`style.css` 和 `script.js`。
- HTML 和 MULTI_FILE 通过 `VersionedArtifactStore` 发布不可变版本，使用 manifest 哈希、请求墓碑、单调序号和原子活动指针。
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
| `controller/InternalAiToolsController.java` | 内部文件、校验、发布和构建工具边界 |
| `core/artifact/HtmlArtifactParser.java` | 严格解析单文件 HTML |
| `core/artifact/HtmlArtifactValidator.java` | HTML/CSS/JavaScript 确定性校验 |
| `core/artifact/SeleniumHtmlSmokeTester.java` | HTML 浏览器烟测 |
| `core/artifact/ArtifactPublicationService.java` | 解析、校验、烟测和发布编排 |
| `core/artifact/VersionedArtifactStore.java` | 不可变 release、manifest、墓碑和活动指针 |
| `core/artifact/HtmlOutputBudgetGuard.java` | 大型 HTML 整页重写预算保护 |
| `service/impl/AppServiceImpl.java` | 生成入口、租约、历史和 SSE 生命周期 |

### Python AI 服务

| 文件 | 作用 |
| --- | --- |
| `ai-service/src/ai_service/app.py` | FastAPI 应用工厂和生命周期 |
| `api/routes.py` | 健康、路由、流式生成和取消接口 |
| `api/schemas.py` | 内部请求、响应和事件模型 |
| `orchestration/workflow.py` | LangGraph 主工作流和发布后终态处理 |
| `orchestration/events.py` | 事件序号和异步队列 |
| `orchestration/cancellation.py` | Python 进程内协作式取消状态 |
| `models/openai_compatible.py` | DeepSeek/OpenAI 兼容模型适配 |
| `models/tool_contract.py` | Vue 模型工具提示与出站校验 |
| `contracts/internal-ai-tools-v1.json` | Java/Python 共享的版本化工具契约 |
| `infrastructure/checkpoint.py` | Redis checkpoint 和 LangGraph saver |
| `infrastructure/spring_tools.py` | Spring 工具网关客户端和发布重试 |

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
  -> context_prepare
  -> HTML: generate_html
     MULTI_FILE: generate_multi_file
     VUE_PROJECT: vue_agent <-> Spring tools
  -> artifact_validation
  -> project_build
  -> quality_review
  -> repair（不通过时最多 2 次）
  -> HTML/MULTI_FILE: artifact_publish
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

2026-09-21 已记录的验证结果：

- 后端定向测试通过：`CodeParserTest`、`HtmlArtifactValidatorTest`、`ArtifactPublicationServiceTest`、`SeleniumHtmlSmokeTesterTest`、`AiCodeGeneratorFacadeTest`、`InternalAiToolsControllerTest`、`AppServiceGenerationCancellationTest`、`AppControllerSseTest`。
- `mvn -q clean -DskipTests compile` 通过。
- 前端 `optimizePrompt`、`generationStreamProgress`、`previewRefreshCoordinator` 共 14 项测试通过。
- 前端 `npm run type-check` 和 `npm run build-only` 通过；构建仍有既有的大 chunk 警告。
- 浏览器确认简短优化提示包含图片保护要求，冗余生成提示已移除，加载图编译样式为固定 1:1 比例。
- 事故应用旧产物被确认含自然语言前缀并缺少 `</script>`、`</html>`；未对该产物执行覆盖或新生成。

本轮没有以真实模型完成新的三类型端到端生成，因此不能据此声称真实 DeepSeek、真实 Redis 恢复或完整跨服务生成已经通过。

## 8. 关键提交

后端与文档：

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

## 9. 已知限制与风险

### P0：全量生产切换前必须验证或修复

1. **真实三类型端到端仍缺少最新验收。** 需要在真实 Spring、Python、Redis、模型和文件系统环境中分别生成 HTML、MULTI_FILE、VUE_PROJECT，并验证发布、预览、停止和二次优化。
2. **内部工具调用结果幂等仍是进程内 Map。** `InternalAiToolsController` 使用静态 `ConcurrentHashMap`，重启丢失、多实例不共享且没有 TTL。它不同于已实现的不可变产物发布幂等。

### P1：稳定性与契约问题

1. 灰度比例仍需确认是否稳定使用用户维度以及 `graySalt`；不能仅凭配置存在就认为路由稳定。
2. Python 对三种模式都经过 `project_build`，需要继续确认 HTML 和 MULTI_FILE 的构建语义是否符合部署要求。
3. Python 路由接口在应用创建阶段可能拿不到 appId，需要统一白名单按 appId 还是 userId。
4. `LangGraphAiGenerationGateway` 使用 JDK HttpClient 和虚拟线程，连接池、超时和断连传播仍需真实压力验证。
5. Python `CancellationRegistry` 是单进程状态，多 worker 或多实例不共享。
6. `completed` 仍可能携带完整 artifact，存在大事件和重复数据风险。
7. 工具参数和返回值仍主要依赖运行时 Map，没有共享 OpenAPI/JSON Schema 契约。

### P2：生产化工作

- 拆分两个调用方向的服务令牌并支持平滑轮换。
- 增加模型超时、重试、限流、token 使用和各节点耗时指标。
- 完善 requestId、appId、userId、engine、node、model、tool 和 status 结构化日志。
- 增加真实 Redis、真实工具、真实构建、真实浏览器和真实模型的持续集成测试。
- 灰度稳定后再评估删除 Legacy LangChain4j 和历史 LangGraph4j 实验代码。
- 检查并轮换仓库历史中可能暴露的 AI、OSS、邮件等凭据。

## 10. 下一阶段建议顺序

1. 在独立测试应用上完成 HTML、MULTI_FILE、VUE_PROJECT 的真实生成和二次优化验收，不复用事故应用。
2. 将内部工具调用幂等迁移到 Redis，加入 TTL 和 `appId + requestId + toolCallId` 作用域。
3. 压测大流式响应、客户端停止、网络中断和提交竞争，验证 2000 字符窗口与取消状态门。
4. 修正灰度稳定性、路由降级和多实例取消状态。
5. 补齐可观测性和安全加固后，再提高 LangGraph 灰度比例。

## 11. 下一轮开始前检查清单

- [ ] 阅读本文和根目录 `AGENTS.md`。
- [ ] 分别检查后端和前端仓库的 `git status --short`。
- [ ] 保留 `projects/`、本地 `.env`、前端既有 `package-lock.json` 等用户文件。
- [ ] 不自动启动 Python AI 服务；真实验收前确认用户是否已从 IDE 启动。
- [ ] 使用 `rg` 重新确认 Java、Python 和 Vue 实际调用链。
- [ ] 代码生成改动同时检查三种生成类型、发布终态、取消和错误码。
- [ ] 前端流式改动检查 2000 字符、80ms、停止、卸载、历史回源和单次预览刷新。
- [ ] Java 改动运行相关测试和干净编译；Python 改动运行 compileall、pytest、lock check；前端改动运行 Node 测试、类型检查和构建。
- [ ] 执行 `git diff --check`，只提交任务相关文件。
- [ ] 对未运行的真实模型、Docker 或端到端测试明确说明。

## 12. 不可擅自处理的内容

- `projects/` 下的用户生成文件。
- 用户本地 `.env`、`application-local.yml` 和其他凭据。
- 前端仓库中与当前任务无关的 `package-lock.json` 修改。
- 与 AI 链路无关的历史业务代码和乱码注释。
- 未经用户明确要求，不强制重置、删除分支、清理工作树或推送远端。
