# Yu AI Service

`yu-ai-service` 是 yu-ai-code-mother 的 AI 执行服务，基于 Python 3.12、FastAPI、LangChain 和 LangGraph，负责代码生成、工具编排、质量审查与有限自动修复，也提供可选的知识库客服功能。

Spring 负责用户权限、业务数据库、项目文件、构建和发布。Python 通过内部工具网关使用这些能力，不直接连接业务 MySQL 或读写生成项目目录。完整平台的启动步骤见 [项目 README](../README.md)。

## 主要能力

- **三类生成**：单页 HTML、多文件网页、Vue 工程。
- **生成编排**：输入审核、需求整理、生成、验证、有限修复与发布。
- **流式输出**：通过 NDJSON 向 Spring 返回内容和进度，由 Spring 转换为前端 SSE。
- **请求状态**：支持协作式取消和可选的 PostgreSQL checkpoint。
- **知识库客服**：支持文档解析、向量索引、检索、重排及带来源的回答，默认关闭。

## 本地启动

### 1. 准备环境

需要 Python **3.12**、uv、可访问的 OpenAI 兼容模型服务，以及已配置的 Spring 后端。启用 checkpoint 时还需独立 PostgreSQL 数据库。

以下 PowerShell 命令均在 `ai-service/` 目录执行：

```powershell
uv sync --frozen --python 3.12
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

### 2. 配置模型与内部连接

编辑 `.env`，核对以下内容；示例中的密钥和令牌需要自行替换：

```dotenv
AI_SERVICE_MODEL_API_KEY=replace-with-provider-key
AI_SERVICE_MODEL_BASE_URL=https://api.deepseek.com/v1
AI_SERVICE_MODEL_NAME=deepseek-chat

AI_SERVICE_INTERNAL_BEARER_TOKEN=replace-with-shared-internal-token
AI_SERVICE_SPRING_GATEWAY_BASE_URL=http://localhost:8123/api/internal/ai-tools
AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN=replace-with-shared-internal-token
```

两个内部令牌必须与 Spring 的 `ai.token` 相同。Spring 默认通过 `AI_SERVICE_INTERNAL_BEARER_TOKEN` 环境变量读取该值；Spring 服务地址 `AI_SERVICE_URL` 默认为 `http://localhost:8000`。

Spring 不读取 Python 的 `.env`，两端需分别配置。样例默认使用宿主机直启地址 `localhost`；Docker 容器访问宿主机时，工具网关和 PostgreSQL 地址需改为 `host.docker.internal` 或实际服务地址。

从原项目迁移时，优先复用原项目的私有 `.env`，不要用 `.env.example` 覆盖有效配置。Spring 的私有 `application-local.yml` 复用原业务配置，去除旧 Java AI 框架配置，并将 `ai.token` 与原 Python 的两个内部令牌统一。修改后重启两端；IDE 的环境变量会覆盖文件中的同名配置。私有配置及备份均不提交 Git。

若 `/internal/v1/route` 返回 503，先检查模型凭据及输入审核失败原因：示例密钥会被供应商拒绝，这与 checkpoint 连接超时是两个独立问题。`/health/ready` 返回 200 仅证明就绪条件满足，不能证明模型凭据有效。PostgreSQL 超时应检查实际运行位置、主机地址和端口，不要通过关闭输入审核或 checkpoint 掩盖配置错误。

### 3. 选择是否启用 checkpoint

首次本地体验、不需要持久化工作流状态时，在 `.env` 中设置：

```dotenv
AI_SERVICE_CHECKPOINT_ENABLED=false
```

需要持久化时，先创建独立 PostgreSQL 角色和数据库，再配置：

```dotenv
AI_SERVICE_CHECKPOINT_ENABLED=true
AI_SERVICE_CHECKPOINT_POSTGRES_URL=postgresql://yu_ai_checkpoint:replace-with-password@localhost:5432/yu_ai_checkpoint
AI_SERVICE_CHECKPOINT_AUTO_SETUP=true
```

服务启动时会自动初始化所需表。若数据库由管理员预先初始化，可关闭 `AI_SERVICE_CHECKPOINT_AUTO_SETUP`；需要强制依赖 checkpoint 时，设置 `AI_SERVICE_CHECKPOINT_REQUIRED=true`。

### 4. 启动与检查

```powershell
uv run python -m ai_service.server
```

默认监听 `0.0.0.0:8000`。Windows 请使用上述入口，它已配置兼容异步 PostgreSQL 的事件循环。修改 `.env` 后需重启服务。

另开终端检查：

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

`live` 检查进程存活，`ready` 检查 checkpoint 状态。关闭 checkpoint 时也可返回就绪；两者均不验证真实模型、Spring 工具或客服问答。启动 Spring 和前端后，通过实际生成应用验证完整链路。

## 常用配置

配置从当前工作目录的 `.env` 和进程环境变量读取，环境变量优先。下表为代码默认值，完整选项见 [.env.example](.env.example) 和 [config.py](src/ai_service/config.py)。

| 环境变量 | 默认值 | 用途 |
| --- | --- | --- |
| `AI_SERVICE_MODEL_MAX_TOKENS` | `8192` | 模型输出额度 |
| `AI_SERVICE_MODEL_CONTEXT_WINDOW_TOKENS` | `65536` | 上下文窗口，需匹配供应商能力 |
| `AI_SERVICE_INPUT_REVIEW_ENABLED` | `true` | 输入语义审核 |
| `AI_SERVICE_PROMPT_REWRITE_ENABLED` | `true` | 辅助整理需求，保留原始输入 |
| `AI_SERVICE_FRONTEND_SKILLS_ENABLED` | `true` | 内置前端生成规则 |
| `AI_SERVICE_MAX_REPAIR_ATTEMPTS` | `2` | 最大自动修复次数 |
| `AI_SERVICE_VUE_MAX_TOOL_CALLS` | `40` | Vue 生成与修复共用的工具总额度 |
| `AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED` | `false` | Vue 多角色审查 |
| `AI_SERVICE_CHECKPOINT_ENABLED` | `true` | 工作流状态持久化 |
| `AI_SERVICE_CHECKPOINT_REQUIRED` | `false` | checkpoint 不可用时是否阻止执行 |
| `AI_SERVICE_CHECKPOINT_AUTO_SETUP` | `true` | 启动时初始化 checkpoint 表 |
| `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED` | `false` | 知识库客服 |
| `LANGSMITH_TRACING` | `false` | 可选的脱敏旁路追踪 |

## 可选：知识库客服

客服使用独立的 Embedding 和 Milvus 配置，回答模型复用 `AI_SERVICE_MODEL_*`。启用前需准备以下依赖：

| 配置 | 说明 |
| --- | --- |
| `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED=true` | 开启 Python 客服组件；Spring 也需开启 `AI_CUSTOMER_SERVICE_ENABLED` |
| `AI_SERVICE_CLOSEAI_API_KEY`、`AI_SERVICE_CLOSEAI_BASE_URL` | Embedding 服务凭据与地址 |
| `AI_SERVICE_RAG_EMBEDDING_MODEL`、`AI_SERVICE_RAG_EMBEDDING_DIMENSION` | 默认 `openai:text-embedding-3-large`、`3072`，需与向量库一致 |
| `AI_SERVICE_MILVUS_URI`、`AI_SERVICE_MILVUS_TOKEN` | Milvus 连接信息 |
| `AI_SERVICE_MILVUS_COLLECTION_ALIAS` | 知识别名，需与 Spring 配置一致 |
| `AI_SERVICE_RAG_OSS_ALLOWED_HOSTS` | 允许下载知识文档的 OSS HTTPS 主机白名单；Spring 需配置 OSS |
| `AI_SERVICE_RAG_RERANKER_PROVIDER` | 默认 `local_cross_encoder`，需要 CUDA；无 GPU 时可设为 `disabled` |

默认本地重排模型为 `BAAI/bge-reranker-v2-m3`，同一模型与设备只允许一个进程持有。禁用重排时不要设置 `AI_SERVICE_RAG_MIN_RERANK_SCORE`；当前没有可直接通过 `remote_api` 启用的远程重排实现。

知识文档通过 Spring 管理端上传和维护。已有知识库升级应先阅读 [知识库 v2 迁移](../doc/customer-service-knowledge-v2-migration.md) 和 [权威发布迁移](../doc/customer-service-knowledge-publication-migration.md)。权威发布默认关闭；持久启用后不能通过关闭配置退回旧模式。

客服健康接口为 `GET /internal/v1/customer-service/health`，需要内部 Bearer 令牌。健康检查不发送真实聊天或 Embedding 请求，实际问答效果需另行验证。

## Docker 运行

在本目录执行：

```powershell
docker build -t yu-ai-service:local .
docker run --rm --name yu-ai-service -p 8000:8000 --env-file .env yu-ai-service:local
```

容器不会自动创建 Spring、PostgreSQL 或 Milvus。Docker Desktop 下连接宿主机服务使用 `host.docker.internal`；同一 Docker 网络中的服务使用各自服务名。容器中的 `localhost` 指向容器自身。

现有镜像未配置 GPU 运行环境，启用本地重排器时需另行准备。内部令牌和模型密钥通过运行环境注入，不写入镜像。

## 接口与运行边界

业务接口均要求 `Authorization: Bearer <内部令牌>`，由 Spring 调用；健康接口无需鉴权。

| 接口 | 用途 |
| --- | --- |
| `POST /internal/v1/route` | 选择生成类型 |
| `POST /internal/v1/generations:stream` | 执行生成，返回 NDJSON 事件流 |
| `POST /internal/v1/generations/{request_id}:cancel` | 取消指定应用的当前请求 |
| `POST /internal/v1/customer-service/answers` | 知识库问答 |

完整路由与请求字段见 [routes.py](src/ai_service/api/routes.py) 和 [schemas.py](src/ai_service/api/schemas.py)。Python 内部 JSON 接口不使用 Spring 的 `BaseResponse` 包装。

- HTML 和多文件网页通过校验后由 Spring 发布；Vue 直接修改工程文件，取消或失败不保证回滚。
- checkpoint 用于请求级状态，不是长期记忆；目前没有生产自动续跑、任务重新认领或事件断线重放。
- 默认允许 checkpoint 不可用时降级执行，此时 `ready` 返回 503；需要严格持久化时开启 `AI_SERVICE_CHECKPOINT_REQUIRED`。
- 自动构建与模型审查不代表页面所有交互均已验证。模型工具历史有独立大小限制，扩大上下文窗口不会自动提高该限制。
- `.env`、日志、调试缓存和 checkpoint 可能包含敏感信息，不应提交到仓库。checkpoint 的完整状态可能包含提示词与源码。

## 测试与调试

常规检查在本目录执行：

```powershell
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

常规测试使用 Fake 或注入式 Provider，不调用真实模型。PostgreSQL 等集成测试需显式启用并准备对应资源；离线测试通过不等于真实模型或端到端验收通过。资源测试要求见 [开发协作规范](../AGENTS.md)。

需要可视化调试时使用下文的 Studio；IDE 调试另见 [PyCharm 调试说明](../doc/knowledge-pycharm-debug.md)。

## LangSmith 监控与 Studio 图调试

### 已有接入及配置

业务服务在 `app.py` 组装 [LangSmithTracer](src/ai_service/infrastructure/langsmith_tracing.py)，由生成工作流和节点记录有限元数据。SDK 已在 `pyproject.toml` / `uv.lock` 中，无需重新接入。复用原私有 `.env` 的配置：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=填写已有的LangSmith密钥
LANGSMITH_PROJECT=Yuki
```

这里的 `Yuki` 对应本次本地配置；可以选择自己的项目名，endpoint 必须匹配账号区域。业务追踪器要求 tracing、endpoint、key 同时有效；仅配置 key 不会启用。`LANGGRAPH_API_KEY` 兼容别名只在 Studio 启动入口中处理，业务监控使用 `LANGSMITH_API_KEY`。

在 `ai-service/` 运行并保持 Spring、前端可用：

```powershell
uv sync --frozen --python 3.12
uv run python -m ai_service.server
```

修改配置后需重启 Python。前端实际生成应用后，到 [LangSmith](https://smith.langchain.com) 的 Tracing 中选择配置项目。通过 `request_id` / `app_id` 元数据关联 `input_guard`、`context_prepare`、生成/审查/修复节点与请求级 `generation` 记录。业务成功/失败以元数据 `status`、`error_code` 判断，不把 LangSmith 行本身没有 SDK 异常等同于业务成功。

当前实现通过官方 `RunTree` API 发送独立摘要，并显式关闭业务图的自动全量追踪。可以查看节点耗时、生成类型、修复/工具次数及白名单诊断；inputs/outputs 有意为空，不上传原始需求、源码或完整模型对话。不是嵌套的完整调用树，不保证每个节点都有记录，也没有模型 token/费用明细。旁路队列饱和或网络超时可能丢记录，不改变业务终态。

若界面没有记录，依次检查项目名、密钥所属区域/权限、进程环境变量覆盖、Python 重启，以及日志中的 `LangSmith tracing failed`。现有发送超时较短（连接/读取各 1 秒），网络慢时可能丢追踪；`/health/ready` 不验证 LangSmith。不要通过关闭脱敏或打印密钥排查网络。

### 启动本地图调试服务

项目已提供 [langgraph.json](langgraph.json)、[studio_server.py](src/ai_service/studio_server.py) 和 [studio_graph.py](src/ai_service/studio_graph.py)。CLI 不在常规业务依赖中；以下命令在临时覆盖环境提供已验证版本，保留现有业务锁：

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-pythonAIOnly-version/ai-service
uv sync --frozen --python 3.12
uv run --no-sync --with "langgraph-cli[inmem]==0.4.43" --with "langgraph-api==0.5.42" python -m ai_service.studio_server
```

入口读取当前目录 `.env`，配置 Windows 子进程 UTF-8，监听 `127.0.0.1:2024`，关闭自动打开浏览器和热重载。修改图源码后需重启该命令。不要使用 `langgraph up` 的默认 8123 端口，它与本项目 Spring 冲突。

- 健康检查：`Invoke-RestMethod http://127.0.0.1:2024/ok`。
- 本地 API 文档：`http://127.0.0.1:2024/docs`。
- [打开 Studio](https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024)，登录 LangSmith 后选择 `yu_ai_generation`，使用 Graph 模式。
- 本地开发服务只监听回环地址；使用同一台电脑的浏览器连接，浏览器询问本地网络访问时按实际调试需要允许。
- 正常业务仍运行在 8000，Spring 仍指向 8000。Studio 使用自己的线程和本地开发存储 `.langgraph_api/`，不加载业务 PostgreSQL 的历史任务，不会自动捕获前端请求。

该入口和 `langgraph.json` 都将 Studio 进程的 `LANGSMITH_TRACING` 设为 false，用于本地图调试；这不改变业务进程的脱敏监控。关闭自动 trace 不代表 Agent Server 完全无网络通信：CLI 仍可能进行鉴权和产品元数据通信。Studio 可见完整状态，调试目录不得提交。

### 节点断点、状态与继续执行

1. 在前端创建专用测试应用，记下其 appId。不要复用正在生成的应用。
2. Studio 选择 `yu_ai_generation` → 新建 Thread → Input 的 **View Raw**，填入下方状态。字段使用 snake_case，不是 FastAPI 请求的 camelCase，也不是通用聊天模板的 `messages`。
3. 点击 **Interrupt**，设置在 `input_guard` **之前**暂停，再 Submit。此时尚未调用审核模型。
4. 将下一个断点设在 `generation_prepare` **之前**，点击 **Continue**。这一步会调用真实输入审核模型和只读 Spring `artifact_context`，可检查 `input_review`、`context.currentArtifact`。
5. 如只核对接线与上下文，到此停止继续执行。修改状态可用 **Edit node state → Fork**，从检查点重跑可用 **Re-run from here**；重跑可能重复执行模型或工具。

```json
{
  "app_id": "替换为专用测试应用ID",
  "request_id": "studio-替换为本次唯一UUID",
  "thread_id": "替换为Studio当前线程ID",
  "prompt": "检查这个现有 HTML 计数页面。",
  "code_gen_type": "HTML",
  "conversation": [],
  "metadata": {},
  "repair_count": 0,
  "tool_call_count": 0
}
```

`code_gen_type` 可为 `HTML`、`MULTI_FILE`、`VUE_PROJECT`，应与测试应用一致。上例采用 HTML，避免 Vue 初次上下文准备自动搜索图片。`request_id` 每次新执行应唯一，同一断点续跑保留该值；`thread_id` 使用 Studio 当前线程 ID，与适配器的请求上下文一致。

图适配器直接运行父图，未经过 Spring 创建任务、持久化仲裁与租约登记。继续执行生成节点会使用真实模型，Vue 工具会修改项目文件；静态发布仍受 Spring 的任务仲裁限制，手填 requestId 不能获得合法发布资格。不要伪造任务记录或绕过网关来让 Studio 发布成功。完整业务生成/发布验收仍从前端或 Spring 入口发起；Studio 的取消、Fork 和本地 checkpoint 不等同于业务任务恢复。

### 本次验证及官方参考

2026-11-23 已使用现有配置读取 LangSmith 项目及近期节点记录，HTTP 200，记录 inputs/outputs 为空；追踪专项 13 项测试通过。已用上述版本启动 Studio，`/ok`、图结构与 schema 返回 200，并验证 `input_guard` 前暂停、继续到 `generation_prepare` 前暂停；没有执行写文件/发布节点。本次验证的是本地 Agent Server API，未替用户完成 Studio 网页登录。

- [官方：手动 RunTree 追踪](https://docs.langchain.com/langsmith/annotate-code#use-the-runtree-api)
- [官方：本地 Agent Server 开发](https://docs.langchain.com/langsmith/local-server)
- [官方：Studio 输入、断点与线程操作](https://docs.langchain.com/langsmith/use-studio)

## 目录与专题文档

```text
src/ai_service/
  app.py           应用组装与生命周期
  config.py        配置定义与校验
  server.py        服务启动入口
  api/             HTTP 路由与请求响应模型
  orchestration/   工作流、子图、节点与评估工具
  models/          模型、Embedding 与重排适配器
  prompts/         生成、审查及客服提示词
  infrastructure/  checkpoint、Spring 网关与向量存储
  contracts/       Spring/Python 工具契约
tests/             单元、契约与集成测试
evaluation/        固定评估样本
```

- [平台介绍与完整启动](../README.md)
- [生成任务、数据库迁移与发布](../doc/generation-task-ledger.md)
- [产品知识库与评估](../doc/knowledge/product/README.md)
- [客服 RAG 逐轮实验与验收记录](../doc/customer-service-rag-rounds-handoff.md)
- [开发协作规范](../AGENTS.md)

历史实验的调用额度、结果和未测项以对应交接记录为准，不代表当前部署状态，也不作为再次执行真实实验的授权。
