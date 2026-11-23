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

Spring 不读取 Python 的 `.env`，两端需分别配置。样例中的 `host.docker.internal` 用于容器访问宿主机，本机直接运行时应改为 `localhost` 或实际服务地址。

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

需要可视化调试时，参考 [LangGraph Studio 入口](src/ai_service/studio_server.py) 或 [PyCharm 调试说明](../doc/knowledge-pycharm-debug.md)。Studio 会调用实际模型和工具，应使用隔离测试应用。

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
