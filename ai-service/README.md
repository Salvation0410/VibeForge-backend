# Yu AI Service

`yu-ai-service` 是 `yu-ai-code-mother` 的 Python AI 服务，使用 Python 3.12、FastAPI、LangChain 和 LangGraph，提供代码生成类型路由、流式生成、工具编排、质量审查及有限修复，也提供可选的客服知识库 ETL 与 RAG 问答。

本文按当前源码组织，配置定义见 [config.py](src/ai_service/config.py)，配置样例见 [.env.example](.env.example)。

## 架构与职责

```text
Vue 前端（独立仓库）
  -> Spring Boot：业务权限、聊天记录、对外 SSE、项目文件与发布
     -> Python FastAPI：内部 JSON / NDJSON 接口
        -> LangGraph：输入审核、生成、验证与修复
           -> OpenAI 兼容模型
           -> Spring 工具网关：文件、图片搜索、构建与静态产物发布
           -> PostgreSQL：请求级 checkpoint
        -> 客服 RAG（可选）：Embedding、Milvus、Reranker、回答模型
```

Spring 是业务 MySQL 和生成项目文件的唯一所有者。Python 不连接业务 MySQL，不直接读写生成项目目录；文件、构建及发布操作通过带 Bearer 鉴权的 Spring 网关完成。知识文档由 Spring 管理，Python 通过授权下载和变更租约完成索引。

## 项目结构

以下列出当前源码模块；省略 `__init__.py`、缓存与本地运行产物。

```text
ai-service/
├── src/ai_service/
│   ├── app.py                         # FastAPI 工厂、依赖组装与资源生命周期
│   ├── config.py                      # Settings、环境变量与配置校验
│   ├── server.py                      # HTTP 服务启动入口
│   ├── studio_graph.py                # LangGraph Studio 图适配器
│   ├── studio_server.py               # Studio Agent Server 启动器
│   ├── api/
│   │   ├── dependencies.py            # 内部 Bearer 鉴权
│   │   ├── routes.py                  # 健康、路由、生成、取消与客服接口
│   │   └── schemas.py                 # 请求、响应、租约及事件模型
│   ├── contracts/
│   │   └── internal-ai-tools-v1.json   # Spring/Python 工具 JSON Schema
│   ├── orchestration/
│   │   ├── workflow.py                # 生成主图与请求生命周期
│   │   ├── state.py                   # 父图状态
│   │   ├── routing.py                 # 验证、构建与质量审查后的条件路由
│   │   ├── events.py                  # 请求级事件序号与输出队列
│   │   ├── cancellation.py            # 协作式取消标记
│   │   ├── active_generations.py      # 在途生成任务登记与主动取消
│   │   ├── input_review.py            # 输入规则检查与语义审核
│   │   ├── image_intent.py            # 图片需求与保留意图识别
│   │   ├── static_generation.py       # 静态文本流与完整候选分离
│   │   ├── vue_tool_loop.py           # Vue 模型轮次、工具执行及预算控制
│   │   ├── multi_agent_review.py      # Vue 三角色并行质量审查
│   │   ├── nodes/
│   │   │   ├── common.py              # 节点事件、取消及状态摘要公共逻辑
│   │   │   ├── input.py               # 输入审核、产物上下文与主动配图
│   │   │   ├── generation.py          # 各类型生成节点
│   │   │   ├── verification.py        # 硬校验、构建、质量审查与静态修复
│   │   │   └── publication.py         # 静态发布与成功终态
│   │   ├── subgraphs/
│   │   │   ├── schemas.py             # 子图输入、内部状态与输出契约
│   │   │   ├── generation.py          # HTML / MULTI_FILE 生成子图
│   │   │   ├── vue.py                 # Vue 工具子图与修复分派图
│   │   │   └── verification_repair.py # 验证、构建、审查与有限修复子图
│   │   ├── document_etl.py            # 知识文档解析、切分与向量索引
│   │   ├── customer_service_rag.py    # 检索、重排、回答与来源校验
│   │   ├── customer_service_health.py # 客服依赖健康摘要
│   │   ├── customer_service_evaluation.py # 注入式离线评估与指标计算
│   │   └── product_knowledge_evaluation.py # 产品语料校验与显式真实评估
│   ├── models/
│   │   ├── base.py                    # 生成模型协议与返回类型
│   │   ├── openai_compatible.py       # OpenAI 兼容聊天模型适配器
│   │   ├── input_review.py            # 输入审核结果与异常类型
│   │   ├── quality_review.py          # Reviewer、问题等级与审查结果
│   │   ├── tool_contract.py           # 工具契约加载与请求/响应校验
│   │   ├── vue_context.py             # Vue 模型工具历史的有界投影
│   │   ├── embeddings.py              # CloseAI/OpenAI 兼容向量模型
│   │   └── reranker.py                # 重排器协议、本地 GPU 与禁用实现
│   ├── prompts/
│   │   ├── routing.py                 # 生成类型路由提示词
│   │   ├── generation.py              # 三种生成类型与修复提示词
│   │   ├── input_review.py            # 输入语义审核提示词
│   │   ├── review.py                  # 单 Reviewer 与三角色审查提示词
│   │   └── customer_service.py        # 基于知识来源回答的提示词
│   └── infrastructure/
│       ├── checkpoint.py              # checkpoint 协议与禁用实现
│       ├── postgres_checkpoint.py     # PostgreSQL saver、状态摘要与过期清理
│       ├── checkpoint_setup.py        # 独立初始化 checkpoint 表
│       ├── spring_tools.py            # Spring 内部工具 HTTP 客户端
│       ├── spring_knowledge_lease.py   # Spring 知识库变更租约校验
│       ├── knowledge_download.py      # 授权知识文档下载与完整性检查
│       ├── milvus_knowledge.py        # 文档向量、collection、alias 与版本管理
│       └── langsmith_tracing.py       # 脱敏元数据旁路追踪
├── tests/                             # 单元、契约与显式启用的集成测试
│   └── fixtures/                      # 合成评估数据和知识文档样例
├── .run/AI Service.run.xml             # PyCharm 启动配置
├── .env.example                       # 可提交的环境变量样例
├── .gitignore
├── Dockerfile
├── langgraph.json                     # Studio 图注册
├── pyproject.toml                     # Python 版本、依赖与测试配置
├── uv.lock                            # 锁定依赖
└── README.md
```

## 本地启动

### 环境要求

- Python `>=3.12,<3.13` 和 [uv](https://docs.astral.sh/uv/)。
- 可访问的 OpenAI 兼容模型服务和 Spring Boot 后端。
- 启用 checkpoint 时需要独立 PostgreSQL 数据库，建议 PostgreSQL 15+。
- 客服 RAG 另需配置 Embedding、Milvus、OSS 授权下载及可选 GPU 重排器。

以下命令在 `ai-service` 目录执行。

### 1. 安装依赖与准备配置

```powershell
uv sync --frozen --python 3.12
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

编辑 `.env`，填写模型密钥与内部令牌。本机直接运行时，至少核对：

```dotenv
AI_SERVICE_INTERNAL_BEARER_TOKEN=replace-with-shared-internal-token
AI_SERVICE_SPRING_GATEWAY_BASE_URL=http://localhost:8123/api/internal/ai-tools
AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN=replace-with-shared-internal-token
AI_SERVICE_MODEL_API_KEY=replace-with-provider-key
AI_SERVICE_MODEL_BASE_URL=https://api.deepseek.com/v1
AI_SERVICE_MODEL_NAME=deepseek-chat
AI_SERVICE_CHECKPOINT_POSTGRES_URL=postgresql://yu_ai_checkpoint:replace-with-password@localhost:5432/yu_ai_checkpoint
```

模型名按供应商实际支持的型号填写。以上令牌、密码和 API Key 均为占位值；样例中的 Spring/PostgreSQL `host.docker.internal` 地址适用于容器访问宿主机，本机运行应改为 `localhost`。

当前双向调用共用 Spring 的 `ai.token`，以下三处必须一致：

```text
Python AI_SERVICE_INTERNAL_BEARER_TOKEN
= Python AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN
= Spring AI_SERVICE_INTERNAL_BEARER_TOKEN
```

### 2. 准备 checkpoint 数据库

由数据库管理员创建独立角色与数据库，设置 `AI_SERVICE_CHECKPOINT_POSTGRES_URL`。默认 `AI_SERVICE_CHECKPOINT_AUTO_SETUP=true`，服务启动时初始化表；也可提前运行：

```powershell
uv run python -m ai_service.infrastructure.checkpoint_setup
```

生产环境建议独立初始化后设置 `AI_SERVICE_CHECKPOINT_AUTO_SETUP=false`。仅本地联调且接受无持久化时，可设置 `AI_SERVICE_CHECKPOINT_ENABLED=false`。

### 3. 启动服务与连接 Spring

```powershell
uv run python -m ai_service.server
```

默认监听 `0.0.0.0:8000`。Windows 使用此入口选择兼容 psycopg 的 `SelectorEventLoop`。修改 `.env` 后需重启服务。

Spring 公共配置默认使用 `langgraph`；在启动 Spring 的终端中核对：

```powershell
$env:AI_ENGINE = "langgraph"
$env:AI_SERVICE_URL = "http://localhost:8000"
# AI_SERVICE_INTERNAL_BEARER_TOKEN 从私有配置或运行环境注入。
```

Spring 不自动加载 Python 的 `.env`。MySQL、Redis、业务表初始化和 Spring 启动步骤见 [项目 README](../README.md)。

### 4. 检查健康状态

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

`live` 只检查进程存活；`ready` 检查 checkpoint，不能证明真实模型、Spring 工具或客服依赖可用。关闭 checkpoint 时 `ready` 可正常返回 200。

## 配置

配置由 `Settings` 从当前工作目录的 `.env` 和进程环境变量读取，进程环境变量优先。除 LangSmith 的 `LANGSMITH_*` 外，主要配置使用 `AI_SERVICE_` 前缀。下表是代码默认值，私有 `.env` 可以覆盖；完整配置项见 [.env.example](.env.example) 和 [config.py](src/ai_service/config.py)。

| 环境变量 | 默认值或要求 | 用途 |
| --- | --- | --- |
| `AI_SERVICE_INTERNAL_BEARER_TOKEN` | 必填 | Spring 调用 Python 的内部鉴权 |
| `AI_SERVICE_SPRING_GATEWAY_BASE_URL` | 必填 | Spring 内部工具基础地址 |
| `AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN` | 必填 | Python 调用 Spring 的内部鉴权 |
| `AI_SERVICE_MODEL_API_KEY` | 真实调用前填写 | 模型 API Key |
| `AI_SERVICE_MODEL_BASE_URL` | `https://api.deepseek.com/v1` | OpenAI 兼容服务地址 |
| `AI_SERVICE_MODEL_NAME` | `deepseek-chat` | 聊天模型 |
| `AI_SERVICE_MODEL_TEMPERATURE` | `0.1` | 生成温度 |
| `AI_SERVICE_MODEL_MAX_TOKENS` | `8192` | 输出额度 |
| `AI_SERVICE_MODEL_CONTEXT_WINDOW_TOKENS` | `65536` | 模型上下文窗口 |
| `AI_SERVICE_MODEL_CONTEXT_OVERHEAD_TOKENS` | `4096` | 协议开销预留 |
| `AI_SERVICE_INPUT_REVIEW_ENABLED` | `true` | 输入语义审核 |
| `AI_SERVICE_INPUT_REVIEW_TIMEOUT_SECONDS` | `15` | 单次审核超时，秒 |
| `AI_SERVICE_INPUT_REVIEW_MAX_RETRIES` | `1` | 审核失败额外重试次数 |
| `AI_SERVICE_INPUT_PROMPT_MAX_CHARS` | `20000` | 输入字符上限 |
| `AI_SERVICE_VUE_MAX_TOOL_CALLS` | `40` | Vue 生成与修复共用的模型工具总预算 |
| `AI_SERVICE_MAX_REPAIR_ATTEMPTS` | `2` | 验证或质量不通过后的最大修复次数 |
| `AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED` | `false` | Vue 三角色质量审查 |
| `AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS` | `60` | 三角色共享的整体超时，秒 |
| `AI_SERVICE_CHECKPOINT_ENABLED` | `true` | PostgreSQL checkpoint 开关 |
| `AI_SERVICE_CHECKPOINT_REQUIRED` | `false` | checkpoint 不可用时是否阻止继续执行 |
| `AI_SERVICE_CHECKPOINT_POSTGRES_URL` | 配置独立数据库连接 | checkpoint PostgreSQL 地址 |
| `AI_SERVICE_CHECKPOINT_AUTO_SETUP` | `true` | 启动时初始化表 |
| `AI_SERVICE_CHECKPOINT_TTL_SECONDS` | `86400` | 异常退出后 checkpoint 保留期限，秒 |
| `AI_SERVICE_CHECKPOINT_POOL_MIN_SIZE` / `AI_SERVICE_CHECKPOINT_POOL_MAX_SIZE` | `1` / `5` | PostgreSQL 连接池范围 |

上下文窗口应按供应商能力设置，必须大于输出额度与协议预留之和。预算以 UTF-8 字节数作为保守 token 上界，不是精确 tokenizer；实际模型调用前还会检查完整消息载荷。

## 代码生成工作流

```text
input_guard -> context_prepare
  -> html_generation / multi_file_generation / vue_generation
  -> verification_repair
       artifact_validation -> project_build（Vue）-> quality_review
         ^                                           |
         +--------------- repair（有限次数）<--------+
  -> artifact_publish（HTML / MULTI_FILE）
  -> finalize
```

主图调度三个生成子图和验证修复子图，负责输入、发布与唯一成功终态。各子图使用独立输入、内部状态和输出 schema，并继承父图 checkpointer；Vue 修复也通过工具子图执行。

### 输入校验与语义审核

输入规则检查与语义审核由类型路由和生成入口共用。审核结果包括放行、带提示放行、需要澄清和拒绝；审核故障不静默放行。关闭语义审核仍保留非空、控制字符、长度和预算检查。Legacy 链路不接入此审核。

Spring 向 LangGraph 传入最近最多六条用户需求（每条最多 1000 个 UTF-16 字符）及最多 2000 字符的初始需求。每次请求独立建立状态，不自动传递完整历史回复、源码或上一请求的工具历史。Vue 当前产物上下文先提供最多 200 条文件路径，再由模型按需读取。

Vue 模型消息的工具历史独立限制为 24 KiB，优先保留近期完整结果，对较早结果提供明确省略摘要；这不裁剪图状态和完整工具记录。最新结果或固定上下文无法完整容纳时停止本轮，增加模型窗口不会自动提高该工具历史上限。

### 三类生成与工具调用

| 类型 | 生成方式 | 完成条件 |
| --- | --- | --- |
| `HTML` | 单个完整 HTML 文档，模型文本流式输出 | Spring 严格解析、确定性校验、默认浏览器烟测和静态发布 |
| `MULTI_FILE` | 按固定顺序输出 `index.html`、`style.css`、`script.js` 三个代码区块 | Spring 协议校验、产物校验和静态发布 |
| `VUE_PROJECT` | 文件规划与完整 JSON 工具决策，分批读写工程 | 产物校验、Vue 构建和质量审查 |

静态文本流将展示增量与完整候选分开；只有模型正常结束后才发送完整候选用于校验。截断或被过滤的响应不能发布。Vue 默认每轮最多两个工具，格式不合法的轮次不执行工具，每阶段最多额外重试两次。

工具契约位于 [internal-ai-tools-v1.json](src/ai_service/contracts/internal-ai-tools-v1.json)。模型可调用 `dir_read`、`file_read`、`file_write`、`file_modify`、`file_delete` 和 `image_search`；产物上下文、校验、发布、构建及源码快照仅由工作流调用。Python 校验工具请求和响应，Spring 负责应用范围、相对路径和文件权限约束。

首次 Vue 生成默认搜索配图，明确不配图时跳过；后续修改只有明确新增、替换或搜索图片时触发主动检索。普通保留图片或布局调整不强制搜图。检索无结果可降级继续；检索成功时会检查图片 URL 是否写入源码，实际加载效果仍需浏览器验证。

### 质量审查与发布

默认使用单 Reviewer；开启 Vue 三角色审查后，并行检查需求、功能和技术质量。审查在硬校验及构建成功后执行，使用 Spring 返回的真实源码快照。快照最多 24 个文件、单文件 12000 字符、合计 60000 字符；存在截断时不代表已完整审查所有源码。单 Reviewer 模式下，完成过修复的 Vue 也会读取快照。

HTML / MULTI_FILE 由 Spring 写入不可变 release，使用 MySQL 代际和持久化取消/提交资格仲裁切换权威活动指针；Redis 提供应用级串行化和实时状态。`.current` 是新协议的兼容镜像，数据库不可用时不回退该镜像。新协议暂未自动清理 release。

Vue 通过工具直接修改工程目录，尚无相同的不可变版本切换与失败回滚保证。任务账本、心跳、迁移顺序和发布证据详见 [生成任务说明](../doc/generation-task-ledger.md)。

## HTTP 接口与事件

内部业务接口均要求：

```http
Authorization: Bearer <AI_SERVICE_INTERNAL_BEARER_TOKEN>
```

| 方法与路径 | 用途 | 鉴权 |
| --- | --- | --- |
| `GET /health/live` | 进程存活 | 无 |
| `GET /health/ready` | checkpoint 就绪 | 无 |
| `POST /internal/v1/route` | 路由到三种生成类型 | Bearer |
| `POST /internal/v1/generations:stream` | 生成工作流，返回 NDJSON | Bearer |
| `POST /internal/v1/generations/{request_id}:cancel` | 取消指定 appId 的请求 | Bearer |
| `POST /internal/v1/customer-service/answers` | 客服问答及最多三条来源 | Bearer |
| `POST /internal/v1/customer-service/knowledge:etl` | 文档索引 ETL | Bearer + 变更租约 |
| `POST /internal/v1/customer-service/knowledge:delete` | 删除指定文档版本的向量数据 | Bearer + 变更租约 |
| `POST /internal/v1/customer-service/knowledge:rebuild` | 重建 collection 并切换 alias | Bearer + 变更租约 |
| `GET /internal/v1/customer-service/health` | 客服独立健康摘要 | Bearer |

健康接口另兼容 `/internal/v1/health/live` 和 `/internal/v1/health/ready`。请求响应定义见 [schemas.py](src/ai_service/api/schemas.py)，JSON 字段统一使用 camelCase，Python 内部接口不使用 Spring `BaseResponse`。

生成接口返回 `application/x-ndjson`，每行一条事件，包含 `type`、`requestId`、递增 `sequence`、`node`、`data` 和可选 `error`。事件类型为 `content_delta`、`tool_started`、`tool_finished`、`node_status`、`completed`、`failed`。

静态内容的 `data.contentMode=display_delta` 仅用于展示，`candidate` 表示本轮完整候选；`completed` 返回计数与发布/构建摘要，不重复返回完整源码。Spring 适配为前端 SSE：内容使用 `{"d":"..."}`，成功发送 `done`，业务失败发送 `business-error`，失败不再发送 `done`。

取消会登记协作式标记并取消当前实例在途任务。取消响应不代表 Spring 工具已经发生的文件副作用被撤销；取消与静态提交资格以 Spring 持久化仲裁为准。

## Checkpoint 与恢复边界

工作流使用官方 `AsyncPostgresSaver`，线程标识为 `{appId}:{requestId}`，子图使用继承的命名空间。同一数据库中的 `ai_workflow_status` 仅保存脱敏节点摘要；执行期图 checkpoint 可能包含候选源码和有限修复反馈，应限制数据库访问。

成功、失败或取消进入终态后尽力清理对应 thread，TTL 用于异常退出兜底。清理失败不反转已确定的生成结果。`CHECKPOINT_REQUIRED=false` 时可在存储不可用后降级执行，但 `ready` 返回 503；设置为 `true` 时存储启动或写入失败会显式失败。

当前未启用 `PostgresStore` 长期记忆，也没有生产重启自动续跑、任务重新认领或客户端事件重放。恢复测试用于验证底层 checkpoint 与子图状态，不能替代生产恢复入口。

## 客服知识库与 RAG

客服默认关闭，设置 `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED=true` 后才初始化相关运行组件。客服依赖初始化失败会降级客服能力，不改变代码生成 readiness。

```text
知识入库：Spring 上传私有 OSS -> ETL 任务与变更租约
  -> Python 授权下载 -> PDF/DOCX/MD/TXT 解析 -> 切分 -> Embedding -> Milvus

客服问答：问题 -> Embedding -> Milvus 检索 -> Reranker
  -> 回答模型 -> 引用校验 -> 答案与来源
```

| 配置组 | 主要环境变量 | 默认或要求 |
| --- | --- | --- |
| 总开关 | `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED` | `false` |
| 向量服务 | `AI_SERVICE_CLOSEAI_API_KEY`、`AI_SERVICE_CLOSEAI_BASE_URL` | 启用时必填，支持旧的无前缀名称 |
| 向量模型 | `AI_SERVICE_RAG_EMBEDDING_MODEL`、`AI_SERVICE_RAG_EMBEDDING_DIMENSION` | `openai:text-embedding-3-large`、`3072` |
| Milvus | `AI_SERVICE_MILVUS_URI`、`AI_SERVICE_MILVUS_TOKEN`、`AI_SERVICE_MILVUS_DATABASE` | URI 默认 `http://localhost:19530` |
| 知识别名 | `AI_SERVICE_MILVUS_COLLECTION_ALIAS` | `customer_service_knowledge`，与 Spring 配置一致 |
| 切分 | `AI_SERVICE_RAG_CHUNK_SIZE`、`AI_SERVICE_RAG_CHUNK_OVERLAP` | `1000`、`150` |
| 检索 | `AI_SERVICE_RAG_RETRIEVAL_TOP_K`、`AI_SERVICE_RAG_FINAL_TOP_K` | `8`、`3` |
| 重排器 | `AI_SERVICE_RAG_RERANKER_PROVIDER` | 默认 `local_cross_encoder`；可设 `disabled` |
| 重排模型与设备 | `AI_SERVICE_RAG_RERANKER_MODEL`、`AI_SERVICE_RAG_RERANKER_DEVICE` | `BAAI/bge-reranker-v2-m3`、`cuda` |
| 下载限制 | `AI_SERVICE_RAG_OSS_ALLOWED_HOSTS`、`AI_SERVICE_RAG_DOWNLOAD_MAX_BYTES` | 受控 OSS HTTPS 主机白名单；上限 20 MiB |
| 问答预算 | `AI_SERVICE_RAG_ANSWER_TIMEOUT_SECONDS`、`AI_SERVICE_RAG_PROMPT_MAX_BYTES` | `60` 秒、`16384` 字节 |
| 索引保留 | `AI_SERVICE_RAG_COLLECTION_RETENTION_GENERATIONS` | 默认保留两代 |

客服回答复用 `AI_SERVICE_MODEL_*` 聊天模型，Embedding 使用独立配置。知识变更必须由 Spring 签发未过期 mutation lease，Python 不自行创建或延长授权。全量重建先写新 collection，校验后切换 alias；旧版本按保留代数、退役宽限和所有权证明进行有界清理。

本地重排器要求 CUDA，同一模型与设备只允许一个进程 owner；多实例需分配不同 GPU 或另行集成独立重排服务。当前默认应用组装支持本地与禁用实现，不能仅设置 `remote_api` 就认为已有远程服务接入。`AI_SERVICE_RAG_MIN_RERANK_SCORE` 默认不设置，阈值需经评估确定，禁用重排时不能同时设置该阈值。

客服健康接口检查组件生命周期以及 Milvus、Spring 租约校验依赖，不发送真实聊天或 Embedding 请求。RAG 关闭时返回 disabled，依赖不可用时返回降级摘要；这些状态不等于真实问答效果验收。客服不使用生成 checkpoint，也不保存跨请求会话。

客服候选先校验活动版本和元数据，仅按 chunk ID 去重，按原检索顺序最多保留 100 块；同文档多个块全部参与重排。重排请求覆盖全部候选，返回结果校验身份、重复 ID 和有限分数，并始终使用原始候选正文与来源；按分数降序排列，并列保持原检索顺序。阈值仅用于真实重排分数，随后每个文档保留最高分块，重复文档不占名额，最多选择三个不同文档（受 final top-k 配置限制）。禁用或故障降级按原检索顺序执行同样的文档选择。诊断 `selected` 阶段分别标记 `duplicate_document` 和 `top_k`。单块上下文最多 1200 字符、合计最多 3000 字符，最终 JSON 还受 `AI_SERVICE_RAG_PROMPT_MAX_BYTES` 的 UTF-8 字节预算约束。

mutation control collection 的占位向量维度固定为 2，以兼容 Milvus 3.x 对最小向量维度的限制。客服知识健康探测验证服务依赖可用性，但不会证明每个禁用文档的异步 Milvus 删除任务均已完成；请同时检查文档 ETL 任务状态。失败的删除任务可由管理员通过 `POST /api/admin/customer-service/knowledge/{id}/tasks/{taskId}:retry` 重新排队，接口只接受与当前禁用/删除文档版本匹配的失败 DELETE 任务。

## Docker 与调试

### Docker 启动

在本目录构建并启动 Python 服务：

```powershell
docker build -t yu-ai-service:local .
docker run --rm --name yu-ai-service -p 8000:8000 --env-file .env yu-ai-service:local
```

Dockerfile 使用 Python 3.12 和锁定依赖，不自动创建 Spring、PostgreSQL 或 Milvus。Docker Desktop 下访问宿主机服务使用 `host.docker.internal`；容器中的 `localhost` 指向容器自身。Linux 可使用同一 Docker network 的服务名或配置宿主机可达地址。镜像不包含 GPU 运行配置，启用本地 GPU 重排需另行配置运行环境。

### LangGraph Studio 与 PyCharm

```powershell
uv run --with "langgraph-cli[inmem]" python -m ai_service.studio_server
```

Studio 连接 `http://127.0.0.1:2024`，选择 `yu_ai_generation`；图注册在 [langgraph.json](langgraph.json)。图输入使用 `app_id`、`request_id`、`prompt`、`code_gen_type` 等 snake_case 字段，初始 `repair_count` 和 `tool_call_count` 为 0。

Studio 是直接图调试入口，不经过 Spring 生成入口的任务与租约建立；它会调用真实模型和工具，应使用隔离测试应用，不能代替完整生成链路或发布验收。Agent Server 管理调试 checkpoint，项目 PostgreSQL saver 在该入口关闭；本地 `.langgraph_api/` 可能包含提示词与源码，不提交。

PyCharm 可使用 [.run/AI Service.run.xml](<.run/AI Service.run.xml>) 启动 `ai_service.server`。调试细节见 [Studio 说明](../doc/langgraph-studio-debug.md) 和 [PyCharm 说明](../doc/knowledge-pycharm-debug.md)。

### LangSmith

设置 `LANGSMITH_TRACING=true`、`LANGSMITH_ENDPOINT`、`LANGSMITH_API_KEY` 和 `LANGSMITH_PROJECT` 可启用脱敏旁路追踪，默认关闭。仅发送请求/应用标识、节点、状态与有限计数，不发送提示词、对话、源码或工具正文；追踪故障不改变生成终态。Studio 启动器独立关闭自动云追踪。

### 同请求客服 RAG 诊断

`AI_SERVICE_RAG_DIAGNOSTICS_ENABLED=false` 和 `AI_SERVICE_RAG_DIAGNOSTICS_REQUEST_PREFIX=`
是默认值。只有显式开启诊断且请求 `requestId` 匹配非空前缀时，才创建请求局部 trace；
关闭时保留原回答调用形式，候选筛选、预算与回答策略不变。不维护全局请求缓存。

专用 Python logger `customer_service.rag.trace` 默认级别为 INFO、`propagate=False`，
不写入通用日志。运维可通过标准 `logging.config.dictConfig` 配置该 logger 的
`FileHandler` 和 `%(message)s` formatter，收集每行一个 JSON；无需额外生产路径配置。
handler 写入失败不会改变回答。HTTP 超时、断连和外部取消会取消并排空子任务后输出一次日志，
分别标记 `TIMEOUT`、`CLIENT_DISCONNECTED`、`CANCELLED`；取消异常继续向上传递。

结构版本为 `schemaVersion=customer-service-rag-trace/v1`，包含最长 128 字符的 `requestId`、
UUID `traceId`、非敏感配置指纹、阶段耗时以及检索、过滤、重排、阈值、选择和两层预算元数据。
每阶段最多 100 项，仅保存文档/分块身份、版本、排名、有限分数、删除原因和长度；
预算阶段记录裁剪前后长度，模型与引用校验保留未执行的 null 状态。
缺失阶段耗时表示该阶段未执行。终态记录 answered/refused/service_error/cancelled、
稳定原因码和 degraded。日志不会保存问题、答案、正文、向量、异常详情或令牌，
JSON 转义恶意请求标识中的换行。身份 ID 属于诊断数据，采集文件仍应使用运维访问控制。

### 同请求 HTTP 评测

产品知识评测器 `ai_service.orchestration.product_knowledge_evaluation` 默认只校验本地语料。
`--live` 使用本机 FastAPI lifespan 初始化模型、Embedding、Milvus 和重排器；`--http-base-url`
模式则只用现有 `AI_SERVICE_INTERNAL_BEARER_TOKEN` 调用已运行的客服回答 HTTP 接口，不创建应用、
不加载本机 GPU owner，也不额外发送检索请求。HTTP 模式需同时提供 `--run-id`、`--document-map`、
`--trace-file` 和新的 `--output` 路径；输出已存在时拒绝运行。`--case-ids` 可选择条目子集，
此类报告标记 `diagnosticExperiment=true`，不能当作 90 题基线。

每题 requestId 固定为 `<run-id>:<case-id>`，最多 128 字符；评测期间服务会向 JSONL trace 追加记录，评测器按 requestId 关联本轮新增的唯一记录。运行前需在服务进程设置 `AI_SERVICE_RAG_DIAGNOSTICS_ENABLED=true`、`AI_SERVICE_RAG_DIAGNOSTICS_REQUEST_PREFIX=<run-id>:`，并用 `customer_service.rag.trace` logger 的 `FileHandler` 按前文配置收集 JSONL。
报告检查 trace schema、配置指纹一致性、阶段完整性和映射文档版本。每个阶段的 rank 必须严格递增，允许过滤后有排名空缺；过滤、阈值、选择和预算阶段的 kept 必须为布尔值，预算留存状态必须与非负裁剪长度一致。缺失、重复或不匹配时，
相关聚合指标留空并以稳定诊断码列出。成功响应和超时后均最多等待 0.5 秒收集 trace，不重试 HTTP；JSONL 未写完的尾行留待下次读取。模型、引用必需字段、非负有限阶段耗时和唯一排名也必须符合 trace 契约。
`stageMetrics` 分开报告原始检索、真实重排、阈值后、文档选择后和最终上下文文档排名。原始检索报告 `Recall@8`、`MRR@3`、`NDCG@3`；重排及后续候选阶段报告 `MRR@3`、
`NDCG@3` 和 `Coverage@3`（answerable 问题中前三名至少命中一个目标文档的比例），不把最多 3 项的
重排结果称为 `Recall@8`。reranker disabled 或回答降级时不产生真实重排和阈值指标；HTTP 错误、
服务 fallback 或诊断错误也不会被计入正常排名成绩。独立 `answerMetrics` 保留可回答题实际回答率、无答案题安全拒答率、旧严格引用通过率（来源文档 ID 必须唯一）、HTTP 延迟平均/P95、错误及降级；故障保留在比例分母中，不能算为正确拒答。HTTP 延迟不包含等 trace 落盘的时间。
评测请求会触发真实回答模型调用，需使用专用新 run ID、trace 文件和报告文件。HTTP 报告保存完整 answer 和响应 sources 供 90 题人工事实复核，仅允许输出到仓库已忽略的 `tmp/`；CLI 终端只输出无逐题正文的聚合摘要。评测器不自动配置或检查 ACL，`tmp/` 的 Git 忽略规则也不提供访问隔离；运行者须在运行前手动配置并核对目录 ACL，仅允许评测人员访问。不提交 Git、不上传公共日志，复核完成后按本地数据保留策略清理。常规 trace 仍禁止答案与知识摘录，也不保存令牌或异常正文。

## 测试与校验

```powershell
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

常规测试使用 Fake/注入式模型与基础设施，覆盖 API、输入审核、三类生成、文本流、子图状态、Vue 工具与上下文、取消、checkpoint、工具契约、客服 ETL/RAG、健康检查和评估。它们不代表真实模型、GPU、Spring 构建或浏览器端到端验收。

PostgreSQL 生命周期和子图跨连接恢复测试默认跳过。准备独立测试数据库后显式启用：

```powershell
$env:AI_SERVICE_POSTGRES_INTEGRATION = "true"
# 连接通过私有 .env 或 AI_SERVICE_CHECKPOINT_POSTGRES_URL 注入。
uv run pytest tests/test_postgres_checkpoint_integration.py tests/test_postgres_subgraph_integration.py -ra
```

上述测试连接真实 PostgreSQL，模型和 Spring 工具仍使用 Fake。Redis 工具幂等、MySQL 任务及发布的集成测试属于 Spring，入口见 [AGENTS.md](../AGENTS.md)。

客服离线指标由 `customer_service_evaluation.py` 计算，合成夹具位于 `tests/fixtures/customer_service_eval.json`。产品语料可运行无外部连接的结构校验：

```powershell
uv run python -m ai_service.orchestration.product_knowledge_evaluation
```

真实 RAG 评估需显式使用 `--live` 或前述 `--http-base-url` 模式，并提供各模式要求的文档版本映射和报告输出，具体步骤见 [产品知识库说明](../doc/knowledge/product/README.md)。

## 部署约定与相关文档

内部接口部署在受控网络，令牌与密钥通过私有配置或运行环境注入；不提交 `.env`、checkpoint 数据、调试缓存或日志。Python 不挂载生成项目目录。Spring 业务库迁移、代际及发布恢复操作由业务后端管理。

- [项目启动与构建](../README.md)
- [开发协作规范](../AGENTS.md)
- [生成任务、迁移与发布边界](../doc/generation-task-ledger.md)
- [子图架构与交接说明](../doc/ai-service-subgraph-refactor-handoff.md)
- [产品知识库与评估](../doc/knowledge/product/README.md)
