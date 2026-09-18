# AI 服务第一阶段重构交接说明

## 1. 文档用途

本文供下一轮 AI 对话或开发者继续推进 LangChain + LangGraph 重构。内容以 2026-09-18 当前 `dev` 分支源码为准，明确区分已实现能力、尚未完成的设计目标和已知风险。

开始下一轮工作前，先阅读：

1. 根目录 `AGENTS.md`
2. `ai-service/README.md`
3. `doc/ai-service-startup.md`
4. `doc/ai-service-langchain-langgraph-refactor-design.md`
5. 本文

## 2. 第一阶段目标与完成范围

第一阶段目标是在保留 Spring Boot 业务后端的基础上，引入独立 Python AI 服务，并让 Spring 具备切换到真实 LangGraph 编排链路的能力。

已完成：

- 新增 Python 3.12 FastAPI AI 服务和锁定依赖。
- 使用 LangGraph `StateGraph` 实现三类生成分支。
- 使用 LangChain `ChatOpenAI` 适配 DeepSeek/OpenAI 兼容接口。
- 实现 NDJSON 事件流、质量检查、最多两次修复、Vue 工具调用上限和协作式取消。
- 实现 Redis LangGraph checkpoint，默认 TTL 24 小时。
- Spring 新增 `AiGenerationGateway` 及 Legacy、LangGraph、灰度委派实现。
- Spring 保持原有对外 SSE 接口和前端消息格式。
- Spring 新增内部文件与构建工具控制器，包括 Bearer 鉴权、路径沙箱和进程内幂等。
- Python 按 `api`、`orchestration`、`models`、`infrastructure` 职责分包。
- 增加 Python Fake Model 测试、中文 README、启动文档和关键方法中文注释。

第一阶段不是生产完成态。第 10 节列出的差距需要在正式全量切换前解决。

## 3. 当前架构

```text
前端 EventSource
  -> GET /api/apps/chat/gen/code
  -> AppController
  -> AppServiceImpl
  -> DelegatingAiGenerationGateway
     -> LegacyAiGenerationGateway -> LangChain4j
     或
     -> LangGraphAiGenerationGateway
        -> POST Python /internal/v1/generations:stream
        -> LangGraph StateGraph
        -> OpenAICompatibleModel / DeepSeek
        -> SpringToolGateway
        -> POST Spring /api/internal/ai-tools/invoke
  -> StreamHandlerExecutor
  -> 原有前端 SSE
```

Spring 是业务系统和项目文件的唯一所有者。Python 不连接 MySQL，不承担用户登录鉴权，不直接挂载或访问项目目录。

## 4. 服务职责边界

Spring Boot 当前负责：

- 用户登录、应用权限和业务校验
- 应用、聊天历史等 MySQL 数据
- 对外 SSE 入口与旧消息格式
- 项目文件路径、读写、删除和目录读取
- Vue 项目构建、部署和下载
- Legacy LangChain4j 回退链路
- 引擎选择和内部工具认证

Python AI 服务当前负责：

- 生成类型路由接口
- 模型调用和供应商适配
- LangGraph 状态与条件边
- HTML、MULTI_FILE、VUE_PROJECT 分支
- Vue Agent 工具循环
- 产物基础校验、项目构建工具决策
- 模型质量检查与有限修复
- NDJSON 事件、checkpoint 和取消状态

## 5. 关键文件索引

### Spring Boot

| 文件 | 作用 |
| --- | --- |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/AiGenerationGateway.java` | 统一生成和路由契约 |
| `ai/gateway/DelegatingAiGenerationGateway.java` | 根据引擎配置和灰度规则选择实现 |
| `ai/gateway/LegacyAiGenerationGateway.java` | 适配现有 LangChain4j 链路 |
| `ai/gateway/LangGraphAiGenerationGateway.java` | 调用 Python 并转换 NDJSON 事件 |
| `config/AiEngineProperties.java` | `ai.*` 配置绑定 |
| `controller/InternalAiToolsController.java` | 内部文件、校验和构建工具边界 |
| `service/impl/AppServiceImpl.java` | 应用创建路由和聊天生成接入点 |
| `core/handler/StreamHandlerExecutor.java` | 既有流消息处理与聊天记录落库 |
| `src/main/resources/application.yml` | 引擎、Python 地址和令牌配置 |

### Python AI 服务

| 文件 | 作用 |
| --- | --- |
| `ai-service/src/ai_service/app.py` | FastAPI 应用工厂、依赖组装和生命周期 |
| `api/routes.py` | 健康、路由、流式生成和取消接口 |
| `api/schemas.py` | 内部 API 和事件模型 |
| `orchestration/workflow.py` | LangGraph 主工作流 |
| `orchestration/events.py` | 递增事件序号和异步事件队列 |
| `orchestration/cancellation.py` | 单进程协作式取消状态 |
| `models/base.py` | 模型协议、响应和工具调用类型 |
| `models/openai_compatible.py` | DeepSeek/OpenAI 兼容 LangChain 适配器 |
| `infrastructure/checkpoint.py` | Redis checkpoint 与 LangGraph saver |
| `infrastructure/spring_tools.py` | Spring 工具网关 HTTP 客户端 |
| `config.py` | `AI_SERVICE_*` 配置 |
| `ai-service/tests` | Fake Model 单元与契约测试 |

### 文档

- `ai-service/README.md`：完整中文使用手册。
- `doc/ai-service-startup.md`：本地、Docker 和联调启动说明。
- `doc/ai-service-langchain-langgraph-refactor-design.md`：目标架构设计。

## 6. 当前 LangGraph 工作流

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
  -> finalize
  -> END
```

所有节点通过统一 wrapper 执行取消检查、`node_status` 事件和业务 checkpoint。`thread_id` 为 `{appId}:{requestId}`。

内部事件类型：

- `content_delta`
- `tool_started`
- `tool_finished`
- `node_status`
- `completed`
- `failed`

Java 对 HTML 和 MULTI_FILE 的 `content_delta` 直接输出文本；Vue 内容和工具事件转换为现有 JSON 消息格式；状态事件不下发给前端。

## 7. 配置、令牌和启动顺序

本地联调时三处令牌必须一致：

```text
Python AI_SERVICE_INTERNAL_BEARER_TOKEN
= Python AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN
= Spring AI_SERVICE_INTERNAL_BEARER_TOKEN（映射到 ai.token）
```

这是无自动过期的静态共享令牌。使用安全随机值，通过 `.env` 或环境变量注入，不得提交。

主要地址：

```text
Spring: http://localhost:8123/api
Python: http://localhost:8000
Python -> Spring tools: http://localhost:8123/api/internal/ai-tools
Spring Redis: redis://localhost:6379/1
Python Redis: redis://localhost:6379/2
```

推荐启动顺序：

1. 启动 MySQL 和 Redis。
2. 配置 `ai-service/.env`。
3. 启动 Python AI 服务并检查 `/health/live`、`/health/ready`。
4. 设置 Spring 的 `AI_ENGINE`、`AI_SERVICE_URL`、`AI_SERVICE_INTERNAL_BEARER_TOKEN`。
5. 启动 Spring Boot。
6. 使用真实登录用户和应用调用 `/api/apps/chat/gen/code`。

详细命令见 `ai-service/README.md` 和 `doc/ai-service-startup.md`。

## 8. 当前验证基线

2026-09-18 本轮已重新执行：

```powershell
cd ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check

cd ..
mvn clean -DskipTests compile
git diff --check
git status --short
```

验证结果：

- `uv run python -m compileall -q src`：通过。
- `uv run pytest`：`14 passed`，另有 Starlette/anyio 与 LangGraph serializer 的 2 条第三方弃用警告。
- `uv lock --check`：通过，解析 62 个包。
- `mvn clean -DskipTests compile`：退出码 0，完成 211 个 Java 源文件编译。

Python 测试使用 Fake Model，不访问真实模型。Java 历史代码仍可能输出 varargs、deprecated API 和未检查类型警告。

未验证或不能据此声称通过的项目：

- Docker image build
- 真实 DeepSeek 模型调用
- 真实 Redis checkpoint 恢复
- 三种生成模式的完整 Spring/Python/文件/构建端到端流程
- 多 Spring 实例下的工具幂等

## 9. 第一阶段核心 Git 提交

以下提交构成主要实现与整理历史：

```text
ee58523 新增独立 LangGraph AI 服务
f32c618 feat: 接入 LangGraph 生成网关与工具边界
0d8f030 docs: 完善 LangChain LangGraph 重构方案
3b415e2 docs: 增加 AI 服务启动说明
3ad892f refactor: 按职责拆分 AI 服务模块
da0e893 docs: 完善 AI 服务中文使用说明
f938f9d docs: 补充 LangGraph Java 接入中文注释
```

其间的 `docs/superpowers/specs` 和 `docs/superpowers/plans` 提交记录了设计决策与实施过程，可用于追溯，但不是运行依赖。

## 10. 已知限制与实现差距

### P0：切换全量 LangGraph 前必须处理

1. **HTML 和 MULTI_FILE 尚未形成完整保存闭环。** Python 生成 artifact 后只调用基础校验和构建工具，没有调用 Spring 保存解析后文件的工具；不能据此认为生成项目已经落盘。
2. **Spring 尚未把最近聊天历史传给 Python。** 当前生成请求只传 prompt、类型、appId、requestId 和 userId metadata。
3. **路由失败未按设计降级 HTML。** Python 返回未知类型或 HTTP 失败时 Java 当前抛出异常。
4. **客户端断连取消未形成显式双向协议。** Java Flux 取消不一定终止底层 JDK HttpClient 请求，也没有显式调用 Python cancel 接口；Python 只有在自身检测到请求断开或收到 cancel 时标记取消。
5. **工具幂等不是 Redis。** Spring 使用静态 `ConcurrentHashMap`，重启丢失且多实例不共享，也没有 TTL。
6. **Vue 模型工具名尚未与 Spring 对齐。** Python 模型提示和测试使用 `search_reference`，但 `InternalAiToolsController` 当前不支持该工具，真实 Vue Agent 发起该调用会返回“不支持的工具”。

### P1：稳定性和契约问题

1. 灰度比例使用 `Objects.hash(userId, requestId)`，同一用户每次 requestId 不同，不能保证稳定落在同一引擎；`graySalt` 当前未参与计算。
2. Python workflow 对三种模式都进入 `project_build`，需要确认 HTML 和 MULTI_FILE 是否应构建，或改为条件节点。
3. Python 路由接口存在，但应用创建阶段 appId 为空；需要统一白名单究竟按 appId 还是 userId。
4. Spring `LangGraphAiGenerationGateway` 使用 JDK HttpClient 和虚拟线程消费流，没有统一响应式 WebClient 的超时、取消和连接池策略。
5. Python `CancellationRegistry` 是单进程内存状态，多 worker 或多实例不共享。
6. `completed` 中包含完整 artifact，可能造成大事件和重复数据；需要确认 Spring 是否只应接收增量与摘要。
7. 工具网关参数和返回值只有运行时 Map，没有共享 OpenAPI/JSON Schema 契约。

### P2：生产化工作

- 拆分两个调用方向的服务令牌并支持平滑轮换。
- 增加模型超时、重试、限流和 token 使用指标。
- 完善 requestId/appId/userId/engine/node/model/tool/status 结构化日志。
- 增加真实 Redis、真实工具、真实构建和真实模型的集成测试。
- 灰度稳定后删除 Legacy LangChain4j 和历史 LangGraph4j 实验代码。
- 轮换仓库历史中可能暴露的 AI、OSS、邮件等凭据。

## 11. 下一阶段建议顺序

1. 先建立 Java/Python 契约测试，冻结生成请求、事件和工具 schema。
2. 补齐 HTML、MULTI_FILE、Vue 三种模式的保存与构建闭环，并统一 Python/Spring 工具名称及参数 schema。
3. 将聊天历史传入 Python，并设置长度与敏感信息边界。
4. 修复稳定灰度、路由 HTML 降级和连接取消传播。
5. 将工具幂等迁移到 Redis，加入 TTL 和 `appId + requestId + toolCallId` 作用域。
6. 运行真实模型与三模式端到端冒烟，再考虑提高灰度比例。
7. 完成可观测性和安全加固后，才将 `AI_ENGINE` 默认值切换为 `langgraph`。

## 12. 下一轮 AI 开始前检查清单

- [ ] 阅读本文件和根目录 `AGENTS.md`。
- [ ] 运行 `git status --short`，确认用户已有未跟踪文件。
- [ ] 不删除、不覆盖、不提交 `projects/` 下用户文件。
- [ ] 检查当前分支和最近提交，不假定工作树干净。
- [ ] 检查 `ai-service/.env` 是否存在，但不要输出其中秘密。
- [ ] 使用 `rg` 重新确认 Java/Python 实际调用链。
- [ ] 修改接口前同时检查 Java gateway、Python schema、测试和文档。
- [ ] 完成后执行 Python 测试、Java 编译、diff 检查和敏感信息检查。
- [ ] 对无法执行的 Docker、真实模型或端到端测试明确说明原因。

## 13. 不可擅自处理的内容

- `projects/` 下当前未跟踪的用户文件。
- 用户本地 `.env`、`application-local.yml` 和其他未提交凭据。
- 与 AI 重构无关的历史代码、乱码注释和业务模块。
- 未经用户明确要求，不执行强制重置、删除分支、清理工作树或推送远端。
