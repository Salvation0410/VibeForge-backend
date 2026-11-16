# Yu AI Service

`yu-ai-service` 是 `yu-ai-code-mother` 的 Python AI 服务，使用 Python 3.12、FastAPI、LangChain 和 LangGraph，提供代码生成类型路由、流式生成、工具编排、质量审查及有限修复，也提供可选的客服知识库 ETL 与 RAG 问答。

本文按当前源码组织，配置定义见 [config.py](src/ai_service/config.py)，配置样例见 [.env.example](.env.example)。

## 架构与职责

```text
Vue 前端（独立仓库）
  -> Spring Boot：业务权限、聊天记录、对外 SSE、项目文件与发布
     -> Python FastAPI：内部 JSON / NDJSON 接口
        -> LangGraph：输入审核、需求整理与前端规则、生成、验证与修复
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
│   │   ├── generation_response.py     # 生成流断连后的显式关闭与收尾
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
│   │   ├── repair_progress.py         # 静态候选与完整 Vue 快照的请求内判重
│   │   ├── nodes/
│   │   │   ├── common.py              # 节点事件、取消及状态摘要公共逻辑
│   │   │   ├── input.py               # 输入审核、产物上下文与主动配图
│   │   │   ├── preparation.py         # 后台需求整理、规则选择与故障降级
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
│   │   ├── generation_brief.py        # 严格需求摘要与有界原文引用检查
│   │   ├── quality_review.py          # Reviewer、问题等级与审查结果
│   │   ├── single_quality_review.py   # 单 Reviewer 严格判定、拒绝理由与错误边界
│   │   ├── tool_contract.py           # 工具契约加载与请求/响应校验
│   │   ├── vue_context.py             # Vue 模型工具历史的有界投影
│   │   ├── embeddings.py              # CloseAI/OpenAI 兼容向量模型
│   │   └── reranker.py                # 重排器协议、本地 GPU 与禁用实现
│   ├── prompts/
│   │   ├── routing.py                 # 生成类型路由提示词
│   │   ├── generation.py              # 三种生成类型与修复提示词
│   │   ├── input_review.py            # 输入语义审核提示词
│   │   ├── preparation.py             # 结构化需求整理提示词
│   │   ├── frontend_skills.py          # 本地版本化前端辅助规则
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

Vue 单 Reviewer 和多角色 Reviewer 共用当前快照的样式证据要求：判缺失前核对可见 CSS 导入、全局样式、目标组件 scoped 与准确模板类名。全局共享类不必在每个组件重复定义，目标组件自身 scoped 样式可合法生效；未使用的近似选择器不证明目标无样式。基础类已生效而变体未定义时，仍需核对对当前明确需求的实质影响。明确样式/布局缺失仍可阻断，缺文件或截断不能猜缺失。这只是审查提示，不新增 CSS 静态门禁或浏览器视觉验证，也不保证模型不再误判。

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
| `AI_SERVICE_FRONTEND_SKILLS_ENABLED` | `true` | 加载内置、版本化的前端辅助规则 |
| `AI_SERVICE_PROMPT_REWRITE_ENABLED` | `true` | 后台自动整理结构化需求说明，保留原文 |
| `AI_SERVICE_STRUCTURED_EDIT_ENABLED` | `true` | 有效显式单属性编辑省去辅助重写；总重写开关优先 |
| `AI_SERVICE_PROMPT_REWRITE_TIMEOUT_SECONDS` | `15` | 单次需求整理的超时秒数，无自动重试 |
| `AI_SERVICE_PROMPT_REWRITE_MAX_TOKENS` | `1500` | 需求整理的模型输出额度 |
| `AI_SERVICE_PROMPT_REWRITE_MAX_BYTES` | `8192` | 需求说明的 UTF-8 字节上限 |
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
input_guard -> context_prepare -> generation_prepare
  -> html_generation / multi_file_generation / vue_generation
  -> verification_repair
       artifact_validation -> project_build（Vue）-> quality_review
         ^                                           |
         +--------------- repair（有限次数）<--------+
  -> artifact_publish（HTML / MULTI_FILE）
  -> finalize
```

主图调度三个生成子图和验证修复子图，负责输入、发布与唯一成功终态。各子图使用独立输入、内部状态和输出 schema，并继承父图 checkpointer；Vue 修复也通过工具子图执行。

内部 NDJSON 流的 `completed` / `failed` 终态会等待后台图执行及 checkpoint 收尾结束后再投递。终态在内部入队前同步将当前执行标记为收尾，保护终态之后、`graph.ainvoke` 返回之前的图写入；普通内容和进度事件仍实时发送。

执行中的首次主动停止或断连仍取消后台任务。重复停止不再重复注入 `Task.cancel()`；消费者退出通过 asyncio 与 AnyIO 屏蔽等待后台任务排空，外层取消随后继续传播。进入终态维护后保留在途登记，直到清理结束再释放；HTTP 路由及生成专用响应显式关闭迭代器，覆盖 ASGI 2.3 断连通知与 2.4 发送失败，避免依赖垃圾回收收尾。checkpoint 清理失败仍按原规则记录，不反转已确定的业务结果。

这些保护只约束当前进程的生成流生命周期，不撤销 Spring 副作用，不新增恢复、超时强杀或收尾时限；数据库永久挂起、进程强制退出和全部并发时序仍无恢复保证。直接 `run()` 调试调用不具有与 HTTP 流相同的重复取消屏蔽。真实 PostgreSQL 受控事务测试使用 Fake 模型/工具，不能代替真实模型端到端验收。

### 输入校验与语义审核

输入规则检查与语义审核由类型路由和生成入口共用。审核结果包括放行、带提示放行、需要澄清和拒绝；审核故障不静默放行。关闭语义审核仍保留非空、控制字符、长度和预算检查。Legacy 链路不接入此审核。

Spring 向 LangGraph 传入最近最多六条用户需求（每条最多 1000 个 UTF-16 字符）及最多 2000 字符的初始需求。每次请求独立建立状态，不自动传递完整历史回复、源码或上一请求的工具历史。Vue 当前产物上下文先提供最多 200 条文件路径，再由模型按需读取。

Vue 模型消息的工具历史独立限制为 24 KiB，优先保留近期完整结果，对较早结果提供明确省略摘要；这不裁剪图状态和完整工具记录。最新结果或固定上下文无法完整容纳时停止本轮，增加模型窗口不会自动提高该工具历史上限。

### 后台需求整理与前端辅助规则

`generation_prepare` 位于输入审核、当前产物上下文准备之后，三类生成子图之前。两项增强默认开启、可独立关闭；用户无需确认重写结果。原始 `prompt`、生成类型和当前产物不会被摘要覆盖，前端固定的“优化提示”按钮也没有被改成独立模型接口。

服务内置 `prompts/frontend_skills.py` 中人工编写的中文规则：通用设计、可访问性、原生静态/Vue 实现规范。新建应用按关键词选择数据工具、商品或内容页面预设；已有应用选择保留样式规则，并允许用户明确要求的全局改版。每条规则有本地版本和参考来源，运行时只解析本地白名单，不下载或执行第三方 Skill 脚本。这里是适配后的规则接入，不是完整第三方 Skill 执行运行时。

`control-consistency@2` 为新准备结果追加控件一致性指导：同组按钮共享基础样式，区分主次/删除操作，保持间距、窄屏排列，并在已有页面新增控件时沿用相关样式；确认共享样式实际定义和加载，切换 Flex 方向时避免宽度基准变成输入框高度。生成、修复和 Reviewer 共用同一规则，原始需求与结构化编辑范围仍优先；它不是样式硬门禁或视觉审查，不能保证模型落实。旧检查点已有准备结果时不追补新规则，原有 design-core/preserve-style 等版本保持；本轮试验版 control-consistency@1 不在最终白名单中，复用时会被忽略，不自动升级。选择器版本为3，中文“电商”纳入商品预设，仍按现有有限否定规则处理；修改已有页面继续选择保留样式规则。

需求整理复用 LangChain `ChatOpenAI`，以一次异步调用返回严格 JSON，经 Pydantic、原文摘录来源和 UTF-8 字节上限校验。输入包含当前原文、最近六条有界用户需求、初始需求及选定规则引用，不包含项目源码；Vue 的文件清单不能代表已经审查了页面风格。摘要将需求摘录、保留要求、设计建议、假设、交互和验收条件分开，原文始终优先。引用存在不等于语义忠实，后续审查仍需对照原始需求。

超时、供应商错误、无效/截断 JSON 或无效引用会回退原始需求及已选规则；不重试重写请求。取消和硬性上下文预算错误继续终止，不按降级放行。重写单独限制输出额度，新增规则和摘要仍计入实际生成/修复消息预算；Vue 工具历史 24 KiB 上限不变。

解析结果保存在当前请求的 `context.generationPreparation`，包含 `version`、`mode`、规则 ID/版本、`brief` 与 `rewriteStatus`（`ready/fallback/disabled`）。它经窄子图输入传入生成、单/多角色审查和修复；规则文本仅从本地白名单生成系统指令，模型摘要保持为用户消息中的辅助数据。图状态不是跨请求长期设计记忆。

准备阶段只发送简短 `node_status`。配套 Spring 网关将静态状态转换为展示帧，Vue 沿用既有消息格式；状态不能进入静态候选或最终源码历史，迟到状态也不使已有候选失效。未新增 SSE 事件种类、工具权限或数据库结构。第一期不包含截图/多模态视觉审查，不代表成品视觉效果已经实测提升。

详见 [第一期交接文档](../doc/generation-preparation-phase-one-handoff.md)。

### 准备阶段诊断与对照评估

`AI_SERVICE_PREPARATION_DIAGNOSTICS_ENABLED` 默认 `true`，独立于两项增强开关。节点在父图的 `preparation_diagnostics` 保存 `rewriteStatus`、`reason`、`durationMs`、`modelAttempted`、`briefBytes`、`skillIds`，同时输出固定字段 JSON 日志；启用现有 LangSmith 旁路时增加 `generation_preparation_diagnostics` span。诊断不会进入 `context`、模型输入、子图或公共 SSE。关闭诊断不影响增强功能；新请求不再产生这些观测字段。checkpoint 完整状态原本仍包含需求/摘要，不应将观测字段脱敏误认为完整 checkpoint 不含业务内容。

原因码区分 `TIMEOUT`、`PROVIDER_ERROR`、`INVALID_JSON`、`INVALID_SCHEMA`、`INCOMPLETE_RESPONSE`、`INVALID_EVIDENCE`、`BRIEF_TOO_LARGE`；正常/禁用为 `NONE/DISABLED`。取消记录 `CANCELLED` 后继续抛出，硬预算/输入错误记录 `BUDGET_EXCEEDED/INPUT_ERROR` 后继续失败，不降级放行。异常终态仅通过日志/旁路观测，不假装已成功返回节点状态。诊断投影与旁路故障不影响原终态。`modelAttempted` 表示本次是否进入模型尝试，预算发送前拒绝及缓存复用为 false；不表示供应商计费凭证。

选择器版本 `2` 补充英文产品别名，并排除分句内明确否定片段；仍是有限关键词启发式，不能解析全部自然语言否定与转折。本地规则正文版本保持 `1`，已有应用继续优先选择保留样式规则。

在 `ai-service` 目录执行（输出父目录须存在，报告文件必须尚不存在）：

```powershell
# 默认离线，不读取 .env、不创建模型；12 个公开合成样本。
uv run python -m ai_service.orchestration.preparation_evaluation --dataset evaluation/generation-preparation-v1.json --output ../tmp/preparation-offline.json
# 显式真实模式，最多 6 次重写；只连接当前 .env 配置的模型，不要求业务服务运行。
uv run python -m ai_service.orchestration.preparation_evaluation --dataset evaluation/generation-preparation-v1.json --output ../tmp/preparation-real.json --real-model --limit 6
```

三组为 `baseline`（增强全关）、`skills_only`（仅规则）、`enhanced`（规则和重写）。真实模式只对前至多六个样本的 enhanced 各尝试一次，失败占用额度，SDK 禁重试。离线 enhanced 重写标为 `not_run`，缺少摘要的摘录指标为 null。评估直接复用实际准备节点，输入包含原文、类型、存在标志和空历史，不调用审核、项目文件、构建、发布或截图工具。真实模式强制启用这三组对照，生产增强开关不控制评估分组；耗时/字节/输出预算沿用当前配置。

报告只保存样本 ID、配置指纹、版本、固定诊断、原文是否原样保留和聚合指标，不保存需求/摘要正文或供应商 URL。`sourceQuoteCoverage` 只衡量样本指定摘录是否完整出现在 requirements、preserve_constraints 或 evidence 中；不是语义完整度或视觉评分。聚合给出测量分母、成功/降级率、原因和准备节点耗时；P95 使用最近秩法，小样本应谨慎解释。顶层 `rewriteAttempts` 为委托次数（包括调用前预算拒绝），组内同名字段按 `modelAttempted` 统计。硬预算失败单独计入 `rewriteFailed`，不进入成功率分母。

文件以独占创建方式预先占位，防止覆盖并在调用前发现路径问题；中断或不可恢复错误可能留下空/不完整报告，下次必须指定新文件名，不自动补跑。离线测试和准备评估均不能证明页面质量、构建成功率或完整 SSE 可用性。本轮实测与后续建议见 [评估与诊断交接](../doc/generation-preparation-evaluation-handoff.md)。

### 简单修改的可选重写策略

未加引号的目标通过常见中文元素词组或英文位置/元素词表识别；任意目标描述不因包含“按钮”等字样就获准跳过。当前独立集未观察到预计调用减少，默认策略不推广。

`AI_SERVICE_PROMPT_REWRITE_POLICY` 可选 `always`（默认）和 `conservative`。重写总开关优先；关闭重写仍为 `disabled`。启用 conservative 后，仅对已有应用、完整匹配的单项样式/引号文字修改省去辅助模型调用，例如“只把预约按钮改成绿色，保留文字、图片和其他操作”。策略版本位于 `models/rewrite_policy.py`，目前为 `2`。

策略会核对具体元素、显式属性/值或原文/新文案，限制长度和字符，拒绝识别到的否定、指代、全局范围、多元素及附加动作；未完整识别的请求继续原有重写。命中时准备结果和诊断为 `skipped/SIMPLE_EDIT`，`brief=null`、`modelAttempted=false`。它不属于重写成功或故障降级；用户原文、历史与保留规则保持不变，不跳过前置输入审核、后续生成预算、构建或审查。缓存重入沿用准备结果，切换配置不使旧请求重新整理。

当前只是有限语法启发式，无法保证理解任意语言，也没有验证真实生成产物质量；因此默认仍为 always，不能把离线“没有误跳过”理解为生产安全保证。需要试用时将 policy 改为 conservative 并重启 Python；恢复 always 即回退策略，无需数据库迁移。

纯离线策略评估独立于前述三组真实重写工具，不读取 `.env`、不调用模型。前述三组工具继续固定按 always 比较，不受此配置影响。开发集与保留集分开，报告包含正确跳过、误跳过、漏识别、精确率/召回率及预计调用数，不记录需求正文，不提供虚构的耗时或成本节省：

```powershell
uv run python -m ai_service.orchestration.rewrite_policy_evaluation --dataset evaluation/rewrite-policy-development-v1.json --output ../tmp/policy-development-new.json
uv run python -m ai_service.orchestration.rewrite_policy_evaluation --dataset evaluation/rewrite-policy-holdout-v2.json --output ../tmp/policy-holdout-new.json
```

输出父目录须存在、文件须尚不存在。`actualModelCalls` 恒为 0；`predictedAvoidedCalls` 包含误跳过，必须与 `falseSkip` 一起阅读。无分母的精确率/召回率为 null。首批 holdout-v1 已用于诊断，不再作为第二版独立证据；当前独立结果及限制见 [保守重写策略交接](../doc/generation-rewrite-policy-handoff.md)。

### 可视化结构化编辑

前端在已有预览中选中元素后，可单独提交文字颜色、背景颜色、字号或对齐方式。Spring 的生成 GET 接口新增可选 `editIntent` JSON query（最多 2048 字符），Python 内部请求接收同名顶层字段；普通请求保持原路径。结构严格为四个字符串字段：`version="1"`、`selector`、`property`、`value`，拒绝未知字段。颜色仅六位十六进制，字号仅 1–96px 整数，对齐仅 left/center/right。selector 为有限 `body > tag:nth-of-type(n)` 父子路径，最多 12 段、512 字符。

三端生成/核对完全相同的中文原文，不能附加自由文本；Spring 校验权限和原文，Python 在入口及工作流重验。只有顶层意图可触发跳过，metadata 中同名字段不能触发。还要求 Spring 返回已有产物；Vue 的 exists 只表示目录，因此额外要求有界 entries 清单至少包含一份 `.vue/.html/.js/.ts/.jsx/.tsx` 文件。该检查不证明可构建或 DOM 定位存在。前端核对当前 DOM 唯一匹配，后端只校验路径语法，后续模型仍须确认源码定位。

默认 `AI_SERVICE_STRUCTURED_EDIT_ENABLED=true` 时，有效结构化请求标为 `skipped/STRUCTURED_EDIT`、`brief=null`、`modelAttempted=false`，与 conservative 无关。关闭此配置恢复有效请求的辅助重写；关闭总重写开关仍为 disabled。输入审核、硬预算、生成、审查、修复、构建和发布保持原流程。缓存重入复用已有准备结果，开关调整作用于新请求。Legacy 明确拒绝结构化请求（`STRUCTURED_EDIT_UNSUPPORTED`）。

前端规则开启时，经过校验的顶层结构化意图还会选择本地 `structured-edit-scope@1`：本轮指定属性是当前修改范围，当前产物/实际源码是保留基线，历史需求只作背景，不补做旧修改。生成、审查和修复使用同一准备结果；规则是模型约束，不是确定性源码补丁，也不能证明修改范围必然正确。

PostgreSQL 状态摘要支持可选 `preparation_diagnostics`，写入前再次投影固定字段；无效诊断省略，不影响既有状态写入，无需新增 SQL 结构。单 Reviewer 使用下述结构化协议；`quality_review_result` 日志只记录 PASS/REPAIR/INVALID_OUTPUT、响应字符数、白名单结束原因、问题数量和固定问题码，不记录证据、建议或源码正文。

启用/回退说明见 [结构化编辑交接](../doc/generation-structured-edit-handoff.md)。三类应用的有限真实验收、失败与未覆盖项，以及本地限额代理、SSE 运行器和浏览器快照工具见 [真实验收交接](../doc/generation-structured-edit-acceptance-handoff.md)。本轮发现过历史修改混入和审查失败，不承诺局部修改必然精确落到源码。

### 生成分支与完成条件

| 类型 | 生成方式 | 完成条件 |
| --- | --- | --- |
| `HTML` | 单个完整 HTML 文档，模型文本流式输出 | Spring 严格解析、确定性校验、默认浏览器烟测和静态发布 |
| `MULTI_FILE` | 按固定顺序输出 `index.html`、`style.css`、`script.js` 三个代码区块 | Spring 协议校验、产物校验和静态发布 |
| `VUE_PROJECT` | 文件规划与完整 JSON 工具决策，分批读写工程 | 产物校验、Vue 构建、有限必填属性检查和质量审查 |

静态文本流将展示增量与完整候选分开；只有模型正常结束后才发送完整候选用于校验。截断或被过滤的响应不能发布。Vue 默认每轮最多两个工具，格式不合法的轮次不执行工具，每阶段最多额外重试两次。

Vue 共用系统提示在首次生成、已有项目修改和修复的首轮即要求只输出一个完整 JSON 对象；计划、读取原因和完成说明放在 `content` 字段内，禁止对象外前言、总结、多段 JSON 和 Markdown 围栏。这是提示约束，解析器对完整围栏的既有兼容及严格拒绝对象外说明的行为不变；提示不能保证模型一定遵循协议，非法轮次仍整轮丢弃并沿用原有限重试。

Vue 进入新的有限修复阶段时，没有本阶段文件规划则以 `modify_files` 提示模型修复已写入的工程；即使本次请求开始时 `currentArtifact.exists=false`，也不重新提示首次规划。有本阶段新规划时仍按 `write_files` 处理待写文件。修复入口清除上一阶段的规划、批次重试与格式反馈，本阶段新产生的反馈仍保留；不修改原始产物基线、需求、准备结果、完整工具历史或累计预算。此修正避免错误的阶段提示，不保证模型必然及时结束工具调用；工具内反复读写仍可能耗尽预算，不能视为验证子图已触发 `REPAIR_NO_PROGRESS`。

正式 Vue 修复在本阶段显式成功写入后，若连续四次完整 file_read 都与当前连续读取段先前已见的同路径内容相同，会在纯读取批次全部执行完后交回原验证/强制构建/审查。新路径、内容变化、非读取或失败/未知/截断结果打断计数；单份读取最多 12000 字符和 48000 UTF-8 字节，超界不参与。只在本阶段 filePlan 无待写文件、配图要求已满足时退出，不应用于生成阶段或内部兼容入口。所有模型文件工具仍实际调用、记录并计入原 40 次共享预算；不会因重复读取直接成功，不增加两次修复上限。读取保护从已有历史推导，不单独保存计数。该策略不是全工程无变化证明，也不是 `REPAIR_NO_PROGRESS`。

正式 Vue 修复还增加预算收口：本阶段有 `ok is True` 的 file_write/file_modify 后，连续三个完整成功纯读批次，或剩余共享工具额度不多于四次时，在完整批次结束且无待写文件、配图已满足的条件下交回原验证。新路径也占读取批次；非纯读、失败/未知/截断/超界读取重置纯读批次。低额度条件也可以在完整写入批次后触发，不能跳过同批后续工具。成功写入标记与纯读批次仅在 Vue 子图私有进度中保存，阶段初始化清零、checkpoint 恢复保留，不进入父图、模型或公共事件；旧检查点缺新增字段时从未触发状态开始，不追补旧计数。生成和内部兼容入口不启用。

此策略让构建判断本次修改，不直接判成功、不缓存读取、不增加40次/两次上限，也不保证为下一次修复保留足够额度；合理的大范围核对可能较早进入验证并消耗一次修复机会。Vue 生成提示强调父子必填属性及导入导出一致，修复提示优先处理构建报告涉及文件，禁止为确认完成而遍历重读；提示不能代替确定性检查或浏览器验证。

预算拒绝及交回验证时记录固定字段日志：阶段、原因、修复序号、累计工具数、全请求及本阶段读写/其他工具数、剩余额度、本批请求数及连续纯读批次，不记录路径、源码、工具参数或模型正文。固定交回原因新增 `READ_BATCH_LIMIT`、`LOW_TOOL_BUDGET`。中途异常的 PostgreSQL 节点摘要仍可能停在上个已完成阶段，错误现场应对照本条日志；未改摘要 schema、数据库结构或公共 SSE 协议，也未新增任务恢复。

正式 Vue 修复另在含显式成功（`ok is True`）file_write/file_modify 的完整批次后调用 Spring `vue_source_snapshot`，同批后续工具必须全部执行完。复用完整快照指纹规则，只对明确完整、非空、计数一致的路径/内容集合比较；重复集合或 A→B→A 且无待写文件/配图时交回原验证，不能直接成功。只返回原有快照统计事件，不把快照或指纹加入模型历史；固定日志原因为 `REPEATED_SOURCE_SNAPSHOT`。

批次标记和唯一指纹列表保存于 Vue 子图私有进度，阶段初始化清空，checkpoint 恢复保留，不回传父图。内部扫描不消耗模型工具计数，扫描次数受成功写入批次和原工具预算约束；快照调用 ID 含阶段及批次末工具序号。未知/空/截断集合不参与，也不清空此前有效指纹；首次观察在首次成功写入批次后，不读取阶段开始基线。快照异常沿原工具错误路径传播，不额外重试，取消在扫描前后检查。节点结果保存前中断可能重读实时快照，不保证扫描恰好一次；没有新增生产自动恢复。

快照仍有 24 文件、单文件 12000 字符和总 60000 字符边界，不是全工程原子快照。持续变化、单独删除或不完整集合可能继续耗尽预算；源码重复也不代表页面正确。实现及 Fake 验证见交接第 25 节，自然修复和浏览器验收不能由这些检查替代。

工具契约位于 [internal-ai-tools-v1.json](src/ai_service/contracts/internal-ai-tools-v1.json)。模型可调用 `dir_read`、`file_read`、`file_write`、`file_modify`、`file_delete` 和 `image_search`；产物上下文、校验、发布、构建及源码快照仅由工作流调用。Python 校验工具请求和响应，Spring 负责应用范围、相对路径和文件权限约束。

Spring 的 `project_build` 强制构建在 npm 成功且 dist/index.html 存在后，运行随 JAR 发布的 `vue/component-contract-check.cjs`。它通过官方 Vue SFC/Babel AST 核对静态相对 `.vue` 默认导入与直接运行时 `defineProps`/`defineModel` 中的显式 required 属性；静态属性、具名绑定和 v-model 按实际 prop 名比较。当前只阻断确定缺失的必填项，不验证事件处理器或业务语义。不执行组件模块，不修改源码；按项目 Node 依赖解析规则加载支持 `parse`/`babelParse` 的 Vue compiler。

检查最多遍历 20000 项，选择 24 个 Vue 文件，每文件 12000 字符/读取 48000 字节，总计 60000 字符；不跟随符号链接，排除隐藏路径及 node_modules/dist/build/target。类型 props、Options API、带默认值或无法确定的声明、对象展开/动态绑定、作用域内用法、宏同名合并及 onX 事件属性声明等跳过，只比较可见完整文件；统计记录检查/跳过数与截断标记。这里是独立扫描，不把 Spring 源码快照的文件上限等同于扫描覆盖保证，也不提供原子全工程快照。

`VUE_COMPONENT_CONTRACT_FAILED` 带最多四条有界路径/组件/属性诊断，沿原构建失败流程最多修复两次；Node/编译器/检查器不可用、协议损坏或超时返回 `VUE_COMPONENT_CHECK_UNAVAILABLE`，Python 直接失败，不调用模型修复环境。命令等待上限 15 秒，进程及输出收尾沿用原额外限时；取消保留 `VUE_BUILD_CANCELLED`。`project_build` 的 built/errorCode/message 字段与公共 SSE 不变。通过仅说明已支持的范围未发现阻断问题；已有 dist 的 ensure 快路径不重检，失败不删除或回滚源码/dist，不能当作部署或预览访问拦截保证。

固定读书清单需求的浏览器验收探针位于 `scripts/acceptance_reading_list_checks.mjs`（仓库根目录）。它在隔离页面执行添加、空白拒绝、已读切换、筛选及计数检查，只返回固定检查名和数字；不是生产工作流节点，也不调用模型。计数文案和按钮有明确约定，不能作为任意 Vue 应用的通用功能检查。2026-11-11 新应用真实续验及未触发修复的边界见[交接第 19 节](../doc/generation-structured-edit-acceptance-handoff.md#19-vue-门禁真实续验与读书清单交互探针)。

首次 Vue 生成默认搜索配图，明确不配图时跳过；后续修改只有明确新增、替换或搜索图片时触发主动检索。普通保留图片或布局调整不强制搜图。检索无结果可降级继续；检索成功时会检查图片 URL 是否写入源码，实际加载效果仍需浏览器验证。

不配图识别支持同分句内的有限并列否定，例如“不需要登录、远程后端或外部图片”“无需账号和网络配图”，以及 `without external images`。中文匹配限制在否定词后 80 字符内，使用固定图片修饰词；“不要替换原有图片”等保护要求不作为禁止配图，后句明确新增图片仍按新增请求处理。这是规则识别，不能保证理解所有自然语言否定；真实生成及 Reviewer PASS 也不能替代页面功能检查。2026-11-11 的真实对照与审查漏检见交接第 16 节。

直接否定“不增加/不添加/不新增外部图片”也会禁止首次自动配图，并抑制已有页面的自动搜图。新增动作前紧邻的“不”才参与该判断，不将分句中的任意“不”全局否定；“不但添加图片”以及后句明确要求添加图片继续按请求处理。此修复针对删除增量真实验收发现的误搜图及 VUE_IMAGE_USAGE_MISSING，仍不是通用语义理解，详见交接第 28 节。

强制 Vue 构建在必填属性检查通过后，再运行可信脚本的 `--anchors` 模式，解析 `src/App.vue`（兼容根 App.vue）的静态本地组件闭包和 index.html，不执行组件。仅拦截普通字面同页链接 `href="#name"` 缺少静态 id/命名锚点的情况，标识限80字符的 ASCII 字母/数字/下划线/连字符，最多报告四条，错误码为 `VUE_ANCHOR_TARGET_MISSING`，进入原有限修复。`#top`、空片段、动态 href、点击接管的链接跳过；路由/插件入口、动态 ID、动态组件、未知组件/函数调用、插槽、v-html、自定义指令、缺文件或扫描截断时不作缺失证明。普通入口限直接 createApp(App).mount('#app')，额外插件/脚本跳过；局部数据模块的内部运行行为不在检查范围。

锚点模式复用24文件、单文件12000字符、总60000字符及不跟随链接的扫描边界；index.html 和 main 采用有界读取。不可执行、协议损坏或超时返回 `VUE_ANCHOR_CHECK_UNAVAILABLE`，Python 直接失败，不能让模型修复环境；取消、dist 兼容快路径及失败不回滚边界保持。检查通过不证明锚点唯一、目标可见、路由可用、浏览器滚动或购物流程正确，也不是预览/部署的独立访问门禁。

### 质量审查与发布

失败终态现在区分 `ARTIFACT_VALIDATION_FAILED`、原有构建错误和 `QUALITY_REVIEW_FAILED`。硬校验/质量拒绝耗尽预算时提示当前自动修复次数；Vue 构建已通过而质量拒绝时明确说明该阶段，不再打印已通过校验的空错误列表。修复仍共用默认两次上限，工具仍共用40次预算，构建修复可能占尽质量修复机会；没有新增重试或跳过质量门禁。

质量节点在验证子图私有 `review_diagnostics` 中保存阶段、PASS/REPAIR、阻断问题数和固定问题码，并沿现有 PostgreSQL `ai_workflow_status.status_payload` 保留白名单摘要，终态清理完整图 checkpoint 后摘要仍遵守原 TTL。节点旁路增加同样受白名单限制的 verdict/数量/代码标签；自由文本证据、建议和源码不进入摘要、日志或旁路。多 Reviewer 的非白名单阻断代码映射为 `UNCLASSIFIED`；旧布尔注入模型没有理由，只记录零问题数，不伪造证据。新候选清除上轮诊断；不投影到父图、修复输入、模型消息或公共事件。旧 checkpoint 无字段时保持兼容，无数据库结构变更。

默认使用单 Reviewer，OpenAI 兼容适配器单次调用要求严格 JSON：`{"verdict":"PASS","issues":[]}`，或 `verdict=REPAIR` 加 1～4 条问题。每条仅含 `code`、`summary`（最多 160 字符）、`evidence` 和 `repair_hint`（各最多 240 字符），问题码限 `REQUIREMENT_MISSING`、`FUNCTIONAL_ERROR`、`TECHNICAL_ERROR`、`SCOPE_VIOLATION`。PASS 必须无问题，REPAIR 必须有问题；裸 PASS/REPAIR、额外字段、重复键、空理由和超限响应返回 `QUALITY_REVIEW_INVALID_OUTPUT`。长度截断/内容过滤响应即使 JSON 完整也返回 `QUALITY_REVIEW_INCOMPLETE`，不再为格式错误重试或修复。原始响应上限 8192 字符、16384 UTF-8 字节，序列化修复反馈最多 4000 字符。

拒绝依据经既有 `qualityReview.repairFeedback` 传入静态与 Vue 修复，仅作为用户消息中的不可信 JSON 数据，不能覆盖原始需求、结构化编辑范围或工具协议。模型证据不等于确定性验证。新候选硬校验前清除旧理由，避免硬校验失败时误用上轮诊断。旧 Fake/注入式模型仍可返回严格布尔值，其 false 不伪造拒绝理由；实际供应商适配器只接受结构化协议。

开启 Vue 三角色审查后，并行检查需求、功能和技术质量，三角色原有结果协议不变。Vue 单/多 Reviewer 从首次成功构建起均读取 Spring 源码快照。快照最多 24 个文件、单文件 12000 字符、合计 60000 字符；存在截断时不代表已完整审查所有源码。

正式单 Reviewer 由工作流显式传入生成类型；Vue 在供应商适配器内白名单投影审查上下文，只保留当前需求、最多六条用户历史（每条 1000 UTF-16 单元）、初始需求（2000 单元）、当前基线、图片约束、结构化意图、准备结果和验证/构建摘要。本轮 `artifact` 是最新源码快照；生成工具历史、规划/格式反馈、助手历史与任意扩展元数据不再进入审查。此投影不修改图状态、checkpoint 或生成/修复上下文，后两者的 24 KiB 工具历史策略不变。无显式类型的旧适配器调用仍保留历史投影兼容，不能视为生产 Vue 审查入口。

单/多 Reviewer 共用 Vue 组件核对说明：核对可见父子 props、emit/监听与 v-model 的对应关系，以及列表/筛选/计数状态流。构建成功不是浏览器交互验证；合法转发、defineModel、动态组件和不可见/截断文件不能仅凭缺少证据判错。仍是模型审查，不是确定性组件检查器；完整审查消息继续执行硬预算检查，超限不裁剪当前快照、不调用供应商。手写组件级正例位于 `evaluation/vue-review-contract-v1.json`，不包含完整工程或构建证据，也不自动调用模型。

产生 `REPAIR_NO_PROGRESS` 的无进展检测只在当前验证子图内保留 SHA-256 指纹列表，条数受 `AI_SERVICE_MAX_REPAIR_ATTEMPTS`（默认 2）约束；它与上述修复工具阶段交回验证的指纹列表独立。HTML/MULTI_FILE 在硬校验前比较候选原文；重复候选（含 A→B→A）直接以 `REPAIR_NO_PROGRESS` 终止，不再硬校验、送审或发布。Vue 在构建成功后的快照读取与模型审查之间比较路径和内容，不使用模型完成文本或工具调用次数判断源码变化；只有明确完整、非空、计数一致且所有文件无截断的快照参与判重。截断快照仍按原有限审查流程处理；构建失败仍受原修复上限控制。指纹不进入父图、模型或公共事件，不裁剪空白或做语义近似判重；有内容变化也不代表质量改善。快照仅覆盖网关选取的文件，不提供全工程相等证明。

审查解析与无进展机制的初次更新只有离线证据，后续真实授权和验收以交接记录为准。最新审查输入优化的 6 次真实重审与正例检查见交接第 17 节，不能当作新增自然生成端到端或准确率提升证明。没有增加环境开关、数据库结构、工具权限或 SSE 事件类型。仅回退本次输入优化时还原类型传递、Vue 审查投影及专用提示；保留原严格 JSON 解析、快照读取、无进展检测与修复上限。

HTML / MULTI_FILE 由 Spring 写入不可变 release，使用 MySQL 代际和持久化取消/提交资格仲裁切换权威活动指针；Redis 提供应用级串行化和实时状态。`.current` 是新协议的兼容镜像，数据库不可用时不回退该镜像。新协议暂未自动清理 release。

Spring 应用锁使用每次租约独立的 Redisson 逻辑 owner，隔离同客户端同线程复用及迟到异步解锁；此 owner 不是 Python 活动任务身份或 MySQL 执行所有者。相关确定性资源测试不调用模型，也不代表跨标签浏览器、长时间并发或自动恢复已通过，详见[任务账本说明](../doc/generation-task-ledger.md)。

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

默认进程重排器的取消按阶段处理：等待 IPC 锁时取消不释放其他请求的锁；发送阶段取消会终止子进程、关闭连接并排空发送任务后释放锁；等待响应时取消则由后台 drain 持锁至响应排空或进程退出。`close()` 复用同一清理任务，取消调用方不会取消该清理；超出原关闭等待期限会终止子进程。这是重排器已有行为，不适用于生成工作流的 checkpoint 收尾。`tests/test_reranker.py` 通过明确的接收入口、发送门闩、锁等待和关闭阶段信号验证上述规则，不用固定 sleep 猜测取消阶段；使用真实 spawn/IPC 与手写协议，未加载真实 CUDA 模型。

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

### 严格引用 E1 历史配对回放

`python -m ai_service.evaluation.citation_replay --help` 提供冻结历史证据的回放工具；默认仅准备输入，不连接检索、Milvus 或 GPU，也不调用模型。传入 `--candidate --baseline --trace --evidence --dataset --annotations --mapping --output`，输入必须匹配工具中冻结的 SHA-256；Git 换行转换也会造成字节指纹不同，应使用原始受控文件，不放宽校验。输出目录必须不存在，不能覆盖历史记录。

只有显式 `--execute` 才调用配置的 OpenAI 兼容回答接口，可用 `--env-file` 选择私有配置。默认诊断为 23 题 × 两提示 × 三轮，共 138 次；`--max-calls 138 --token-budget 2000000 --max-tokens 8192` 限制调用数和保守 token 预留，不自动重试。`--scope full/remaining` 仅显式选择，完整调用分别需要 540/402 的调用上限，不能凭不完整批次声称全量通过。

输出 `inputs.json` 保存来源、实际裁剪正文、提示、模型参数及指纹；`events.jsonl` 保存调用开始与预算事件；`results.jsonl` 逐次保存回答、引用、延迟、usage 和故障；`summary.json` 包含分层与逐题配对、缺失、预算停止原因。缺失 usage 和未知费用保留 null；回答、引用及参考标签仅用于离线评审，目录应保存在 ignored 的 `tmp/`，勿提交原始运行产物。故障不能算作正确拒答，人工验收保持 pending，工具不自动批准上线。

E1 候选 `CUSTOMER_SERVICE_FOCUSED_SYSTEM_PROMPT` 要求聚焦当前问题、保留必要条件与互补引用，仍未启用。默认 `CUSTOMER_SERVICE_SYSTEM_PROMPT` 使用单独验证的拒答修复 R1；`CUSTOMER_SERVICE_BASELINE_SYSTEM_PROMPT` 永久保留旧 B0。回放默认比较冻结 B0/E1；指定 `--candidate-policy refusal` 则单独比较 B0/R1，升级在线默认提示不会改变实验基线。

R1 明确区分正常的否定业务回答与拒绝执行请求：有依据的公开权限/能力说明可正常回答；要求泄露秘密、取得私人数据、执行客服无能力操作或作未确认承诺，均返回模型内部 `answered=false, answer="", citedChunkIds=[]`。服务对外仍展示既有固定兜底文字及空 sources，不增加字段，不按否定关键词改写模型结果。单变量历史回放中 R1 安全契约 27/27、正常题回答 42/42；这不证明所有未知问题均正确，也不等于 E1 或全链路上线验收。方案与限制见 [拒答修复设计](../docs/superpowers/specs/2026-10-27-refusal-contract-design.md)。

历史快照含已禁用旧手册，回放不能视为当前活动知识库端到端成绩，不重新启用旧手册。前轮 E1 的实验结果及人工复核要求见 [实施记录](../docs/superpowers/plans/2026-10-27-strict-citation-e1-execution.md)。已运行进程不会因修改文件自动加载新提示，须运行包含本次提交的代码后重启生效。

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

生成收尾的确定性回归和真实 PostgreSQL 删除事务门闩测试可分别运行：

```powershell
uv run pytest tests/test_stream_terminal_cleanup.py tests/test_generation_asgi_disconnect.py -ra
# 仅在准备好独立 PostgreSQL 后显式启用；模型与工具仍为 Fake。
$env:AI_SERVICE_POSTGRES_INTEGRATION = "true"
uv run pytest tests/test_postgres_generation_drain_integration.py -ra
Remove-Item Env:AI_SERVICE_POSTGRES_INTEGRATION
```

事务测试为每项创建唯一请求标识，限制过期扫描到本轮标识；在官方 saver 删除语句完成、事务提交前暂停，再注入取消。结束后以独立连接核对三张 checkpoint 表和连接池可用性，仅清除本轮记录。ASGI 测试直接调用真实应用与响应类，不连接网络、Spring 或浏览器。

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

## 客服证据评估与隔离快照对照

`evaluation/customer-service-boundaries-v2.json` 是新增的48题诊断集，含40道可答题、8道拒答题、63条精确原文事实。它不替换固定90题，不是独立盲测集，标注仍待人工复核。

```powershell
# 默认只校验来源、哈希、事实span、问题唯一性与分组，不调用外部依赖。
uv run python -m ai_service.orchestration.rag_snapshot_experiment
# 显式调用真实Embedding、本地GPU重排、回答与抽样语义评审；输出目录必须不存在。
uv run python -m ai_service.orchestration.rag_snapshot_experiment --live --judge --judge-case-limit 12 --max-chat-calls 120 --output ../tmp/rag-study-new-run
# 只读重算逐题差异及问题组指标，导出Ragas常见单轮字段，不追加模型调用。
uv run python -m ai_service.orchestration.rag_snapshot_experiment --analyze ../tmp/rag-study-new-run/report.json
```

真实快照实验用当前1000/150切分的本地公共正文和精确cosine Top8，复用真实BGE重排结果，对比每文档最多1块/2块，总计最多3块，沿用1200/3000字符与JSON字节预算。它不读取业务MySQL、不启动服务、不写Milvus，也不验证生产alias、ANN过滤或文档版本生命周期。运行前确认GPU未被其他服务独占。

全48题两组回答96次，等距抽取12题做两组评审24次，总共预占120次聊天调用；另有4个Embedding批次，最多允许8批。账本发送前落盘，失败/取消计费额度不返还，客户端不自动重试。已有账本和输出目录不可覆盖；重跑是新增费用，须计入当次用户授权总量，不能把更换目录当作绕过额度的方法。

指标分别报告块级不完备标注下的Precision/Recall/MRR/AP/NDCG、精确事实span覆盖、回答/拒答契约、缺失/错误、答案延迟及token usage。语义评审单独评忠实度、相关性与必要事实覆盖，错误保留为空，不折算成通过；同模型评审有偏差，不等于人工正确率。`--analyze`导出的`retrieved_contexts`是实际最终输入，错误响应保留null，拒答题应按契约评，不直接套用空答案忠实度。当前未安装或执行Ragas框架，导出字段仅供后续适配。

完整逐题上下文、回答、向量快照、usage和账本保存在忽略目录tmp；只在交接文档记录脱敏聚合和问题ID，不提交原始运行报告。评估流程、结果与后续方案见[本轮评估交接](../doc/customer-service-rag-evaluation-handoff-20261116.md)。

### 知识版本 v2 基础与下一阶段边界

2026-11-16 续轮实现 B0a：`documentVersion` 表示不可变内容，`indexRevision` 表示每文档索引代际（Spring 使用稳定的 `etlVersion`），`activationRevision` 表示启用资格。新版 INDEX 同时携带正整数索引/激活代际；同一任务重试身份不变。DELETE 撤销该文档不高于目标激活的全部索引，不再用内容版本永久阻止重新启用。控制集合保留不可变激活墓碑及协议升级标记，旧 DELETE/INDEX/REBUILD 不得把已升级文档降回旧协议。

重建顶层 `etlVersion` 只表示全库 buildRevision，各文档保留自己的 serving 索引/激活身份。v2 重建下载、校验并解析原文后，在同一变更许可内核对旧物理集合的完整清单、正文、定位、哈希、模型与维度，复用已验收的原始向量，不再次调用 Embedding；写入前仍逐值复核同一快照。变化时返回 `KNOWLEDGE_VERSION_CONFLICT`，缺旧 v2 清单或块返回 `KNOWLEDGE_REBUILD_REVISION_UNVERIFIABLE`，不降级重算，应先创建新代际 INDEX。Legacy 重建仍调用 Embedding。已有 alias 时空重建返回 `KNOWLEDGE_EMPTY_REBUILD_BLOCKED`。检索还核对 query 配置的模型签名与集合元数据，同维不同模型也拒绝；模型别名不能证明供应商权重未变化。

Spring 保存不可变原文 registry 与 serving 三元组，替换失败后重建可读取旧成功原文。旧库必须先排空任务并执行增量 SQL，再同步部署两端、逐文档新版重索引，最后重建验收；不能混跑旧 worker，不能删除墓碑或直接切旧 alias 回滚。详见[迁移步骤](../doc/customer-service-knowledge-v2-migration.md)。Docker恢复后已执行本机真实备份/SQL迁移及11篇新版INDEX，确认72活动块；首次整库重建冲突后保留原alias，用户确认原向量复用后已部署并成功完成整库重建，72块字段和向量逐值一致、Spring serving身份匹配。B0b 已实现 Spring 权威发布指针、稳定发布账本、alias outbox 与周期对账，以及 Python 私有候选和权威读取；部署迁移需单独完成，见[权威发布迁移](../doc/customer-service-knowledge-publication-migration.md)。这些机制不构成跨系统事务，也不保证在途撤销即时生效。

### B0b 权威发布与读取

`AI_SERVICE_RAG_PUBLICATION_ENABLED` 默认 `false`，候选序列化预算 `AI_SERVICE_RAG_PUBLICATION_MAX_BYTES` 默认 64 MiB；它不提高原文、块数或向量元素预算。候选接口 `/internal/v1/customer-service/knowledge:prepare-publication` 返回原始严格收据，不切 alias、不删除源集合。候选准备复用 ETL 信号量；首个 bootstrap 在尚无 head 的控制模式下仍可准备，最终维护授权、registry 与提交由 Spring 核对，Python 不访问业务 MySQL。

所有生产 RAG 读取及旧 ETL/DELETE/REBUILD 入口每请求向 Spring 核对持久模式，本地开关关闭但 Spring 已启用时拒绝旧协议；缺 schema、接口故障或已启用却缺 head 均失败关闭。维护期尚未 bootstrap 的合法控制快照为 `enabled:false`、当前 epoch 和空 head；本地 publication 开启时该状态允许候选准备，但不能回答知识问题。客服健康检查仅观察控制面、候选元数据和本地依赖，不调用回答或 Embedding 供应商；代码生成健康独立。

publication 读取使用权威具体 collection 和当前身份白名单，检查模型、维度、完成标记、快照 hash 与命中文档身份；不通过 alias 回退。普通检索只取有界元数据和命中标量，不按题下载整库向量；完整向量回读用于准备/验收，不保证检测运维绕过协议后的任意全库篡改。空白名单仍验证合法 head，跳过 query Embedding 和回答模型。请求内独立保存 epoch/head/白名单，模型调用前和返回后复核；变更或数据库故障丢弃答案及来源，不自动重试付费调用。最终复核后仍存在撤销竞态边界。

`/internal/v1/customer-service/knowledge:reconcile-alias` 在写前后核对权威 head，响应固定为 publicationId、epoch、collectionName、reconciled。alias 仅为镜像，失败或迟到不补偿恢复旧镜像、不撤销发布；周期对账在写入静止后收敛。内部 Spring GET 客户端使用现有 Bearer、禁止重定向和环境代理，并对声明长度及解压后 JSON 执行默认 64 KiB 上限；坏类型、未知字段或事实 ID 不匹配均拒绝，不回显响应正文、proof 或签名 URL。

所有 publication 集合保留，旧 GC 仅处理旧 kind，不新增无引用证明的 publication 清理。迁移须排空旧实例、备份、执行 schema 并统一部署，显式维护 bootstrap 后关闭维护入口；禁止混跑旧二进制、切旧 alias 或仅关闭开关回滚。具体操作与未执行部署项以[迁移文档](../doc/customer-service-knowledge-publication-migration.md)和[逐轮交接](../doc/customer-service-rag-rounds-handoff.md)为准。

HTTP 回答入口只接受绑定本应用控制客户端的官方权威 RAG；注入的官方裸 store 服务直接拒绝。旧 Fake/兼容服务必须显式注入 `LegacyAnswerServiceGuard`，生产不会自动包装未知实现；该守卫只保证整个服务调用前后模式检查，不能证明任意自定义内部模型调用前已复核，并且禁止包装官方 RAG 或用于 publication 模式。

### 有界证据与拒答候选实验

```powershell
# 复用上一轮向量，只比较配额并计算 dense Top8/16/24；不调用聊天或 Embedding。
uv run python -m ai_service.orchestration.rag_bounded_evidence_experiment --source ../tmp/rag-evaluation-20261116/live-snapshot --output ../tmp/rag-bounded-new --prepare-title-inputs
# 追加 --local-rerank 才会独占本地 GPU 重排三组；标题输入导出不等于已生成标题向量。
# 拒答成对诊断默认离线校验，依赖固定历史快照或显式 --snapshot。
uv run python -m ai_service.orchestration.rag_refusal_experiment
```

有界证据实验仍总计三块及原字符/字节预算。扩大候选但未重排时最终覆盖保留 null；不得拿旧 Top8 顺序冒充 Top16/24 的结果。本轮同文档 cap3 未比 cap2 增益，Top16/24 的最终证据覆盖反而略降，生产仍保持 Top8、每文档一块。标题与父章节扩展尚未完成质量验证。

`evaluation/customer-service-refusal-pairs-v1.json` 含八条冻结拒答题及八条公开解释反例，仍是开发诊断集。显式 `--live --rounds 3 --max-chat-calls 96 --output <不存在的tmp目录>` 才调用模型；预占落盘、失败占额、无自动重试，连续三次错误终止。R2 仅为候选，生产仍用 R1。本轮真实96次对照中 R2 拒答24/24、公开题24/24可答，但有两条公开回答证据范围存疑，不能据此宣称语义质量通过。原 v2 标注冻结；人工裁决和独立盲测仍待完成。
