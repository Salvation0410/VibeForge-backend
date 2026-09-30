# Yu AI Service

`yu-ai-service` 是 `yu-ai-code-mother` 的独立 AI 编排服务，使用 Python 3.12、FastAPI、LangChain 和 LangGraph 实现代码生成、工具调用、质量检查和有限次数修复。

该服务只负责 AI 能力，不直接访问 MySQL 或项目目录。用户鉴权、应用与聊天记录、文件安全、Vue 构建、部署、下载以及面向前端的 SSE 均由 Spring Boot 后端负责。

## 架构与职责边界

```text
前端 EventSource
    -> Spring Boot 对外 SSE
        -> Python AI Service 内部 NDJSON
            -> LangGraph 工作流
                -> LangChain 模型适配器
                -> Spring 工具网关
                -> PostgreSQL checkpoint
```

| 组件 | 主要职责 |
| --- | --- |
| Spring Boot | 用户鉴权、应用权限、聊天记录、文件操作、项目构建、部署、下载和对外 SSE |
| Python AI 服务 | 模型调用、生成类型路由、LangGraph 编排、质量检查、修复和工具调用决策 |
| PostgreSQL | 独立数据库 `yu_ai_checkpoint` 保存官方 LangGraph checkpoint 和短期脱敏状态，默认 TTL 为 24 小时 |
| DeepSeek/OpenAI 兼容服务 | 提供路由、代码生成、质量检查和修复模型能力 |

Python 只能通过带 Bearer 令牌的 Spring 工具网关读写项目文件或执行构建，不能绕过 Spring 直接操作项目目录。

## 源码目录

```text
ai-service/
├── src/ai_service/
│   ├── app.py                  # FastAPI 应用工厂和依赖组装入口
│   ├── config.py               # 环境变量与服务配置
│   ├── api/
│   │   ├── dependencies.py     # 内部 Bearer 鉴权依赖
│   │   ├── routes.py           # 健康检查、路由、生成和取消接口
│   │   └── schemas.py          # HTTP 请求、响应和 NDJSON 事件模型
│   ├── orchestration/
│   │   ├── workflow.py         # LangGraph 状态图和节点路由
│   │   ├── events.py           # 递增序号事件生成器
│   │   └── cancellation.py     # 协作式取消状态
│   ├── models/
│   │   ├── base.py             # 模型协议和公共返回类型
│   │   └── openai_compatible.py # DeepSeek/OpenAI 兼容适配器
│   └── infrastructure/
│       ├── checkpoint.py       # checkpoint 协议与禁用实现
│       ├── postgres_checkpoint.py # PostgreSQL saver、状态摘要和 TTL 清理
│       ├── checkpoint_setup.py # 独立 schema 初始化命令
│       └── spring_tools.py     # Spring 文件与构建工具网关
├── tests/                      # 不访问真实模型的单元与契约测试
├── .env.example                # 环境变量示例
├── Dockerfile
├── pyproject.toml
└── uv.lock
```

应用的稳定启动入口是 `ai_service.app:create_app`。

## LangGraph 工作流

服务支持三种生成类型：

- `HTML`：生成单页 HTML 产物。
- `MULTI_FILE`：生成多文件静态项目。
- `VUE_PROJECT`：通过 Agent 工具循环生成 Vue 项目。

当前工作流：

```text
START
  -> input_guard
  -> context_prepare
  -> HTML | MULTI_FILE | VUE_PROJECT
  -> artifact_validation
     -> 校验失败：repair（最多 2 次）或 failed
     -> VUE_PROJECT：project_build
  -> quality_review
     -> 质量失败：repair（最多 2 次）或 failed
     -> MULTI_FILE：artifact_publish
  -> finalize
  -> END
```

Vue 分支允许模型请求 Spring 工具，但工具调用次数受 `AI_SERVICE_VUE_MAX_TOOL_CALLS` 限制。每次工具调用都带有确定性的 `toolCallId`，供 Spring 执行幂等控制。

Vue 多 Agent 质量审查开启时，首次生成和每次修复重新通过硬校验、重新构建成功后，都会调用 Spring 工作流专用且模型不可调用的 `vue_source_snapshot`，以当前项目真实源码而不是旧 artifact 作为三个 Reviewer 的审查输入。开关关闭时保持原有兼容行为，仅至少完成一次修复的 Vue 在单 Reviewer 质量检查前读取快照。HTML、MULTI_FILE 分支均不调用该工具。快照最多返回 24 个文件，单文件内容最多 12000 个字符，总内容最多 60000 个字符；Spring 遍历的项目总访问条目（根目录之外的目录、文件和访问失败条目）最多 20000 个，其中合格源码候选最多 10000 个，并只读取按 `package.json`、入口文件、`src/App.vue`、其余路径稳定排序后的最佳 24 个候选，每个源文件最大 1 MiB。

快照排除依赖和构建产物目录、隐藏目录、符号链接、锁文件及非文本扩展名，依赖/构建目录和锁文件的大小写变体同样排除；入选文件执行严格 UTF-8 与 NUL 检查。完整快照只在本次质量检查调用栈内传给当前 Reviewer，不写入 Spring 工具幂等 Redis、业务 checkpoint、LangGraph state/checkpoint 或 NDJSON 事件；快照读取或 Reviewer 系统异常产生的 pending checkpoint 只包含稳定的外层异常，不包含源码，阻断性审查结果则仅按下文规则保存有界 `repair_feedback`。`tool_finished` 事件只公开 `eligibleFileCount`、`includedFileCount`、`omittedFileCount` 和 `truncated` 四个统计字段。快照读取失败使用稳定的脱敏消息，错误响应不包含绝对项目路径；快照读取或 Reviewer 调用失败时不回退到旧 artifact，而是进入失败终态。

### Vue 多 Agent 质量审查

`AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED` 默认是 `false`，只对 `VUE_PROJECT` 生效；关闭时 HTML、MULTI_FILE 和现有 Vue 质量检查行为保持兼容。开启后，Python 并发执行三个只读角色：

- `requirement`：检查用户目标、明确要求和内容完整性。
- `function`：检查主要交互、页面流程和功能可用性。
- `technical`：检查工程结构、运行风险、明显安全问题和可维护性阻塞。

三个角色收到的 `artifact` 都是 Spring 返回的有界真实源码 snapshot；Reviewer `context` 只允许 `prompt`、`codeGenType`、`validation`、`build` 四个字段，并为每个角色创建独立深拷贝。Reviewer 不接收 `conversation`、`metadata`、`currentArtifact`、`toolResults`、`appId`、`requestId` 或其他生成期状态，避免无界内容和内部标识扩散。该白名单只约束审查输入，不改变 repair：Vue repair 工具循环仍保留并继续使用 `toolResults`。

Reviewer 提示词要求模型只返回没有 Markdown 围栏的纯 JSON。为兼容部分 OpenAI 兼容供应商，适配器可防御性剥离单一完整 JSON 围栏，随后仍使用严格 Pydantic schema 校验字段、长度、枚举、额外字段和 Reviewer 身份；这不表示允许围栏外解释、前后缀文字或任意自然语言输出。

三个 Reviewer 共享 `AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS` 指定的整体超时，不是每个角色各自拥有一份超时预算。聚合由确定性代码完成：`critical` 和 `major` 属于阻断问题，会生成最多 4000 字符的 `repair_feedback` 并进入现有 repair 回环；`minor` 只作为本次调用内的审查详情，不触发 repair，也不消耗修复次数。修复仍受 `AI_SERVICE_MAX_REPAIR_ATTEMPTS` 限制，默认最多两次；每次修复后重新执行校验、构建、真实源码快照和三角色审查。

`repair_feedback` 的每个阻断问题固定为单独一行，字段顺序是 `severity`、`code`、`issue`、`evidence`、`repair`。字段值先折叠换行、制表符等空白，再把值内部的 `;`、`=` 规范化为全角分隔符，防止内容伪造结构字段；反馈不添加总标题，也不包含 Reviewer 身份或 category。

任一 Reviewer 超时、普通模型调用失败、返回非法结构，或源码快照失败，都按 F1 直接发送 `failed`，不回退到单 Reviewer、不盲目修复、也不把候选版本当作成功。稳定错误码包括 `MULTI_AGENT_REVIEW_TIMEOUT`、`MULTI_AGENT_REVIEW_MODEL_ERROR`、`MULTI_AGENT_REVIEW_INVALID_OUTPUT` 和 `MULTI_AGENT_REVIEW_SNAPSHOT_ERROR`。用户取消会继续沿用现有协作式取消语义，并取消仍在运行的 Reviewer 任务。Reviewer 边界直接收到 `SystemExit` 或 `KeyboardInterrupt` 时会先脱敏，再等待兄弟任务取消和清理完成后重新抛出；非整数 `SystemExit.code` 统一归一为 `1`，不会泄露原始异常内容。

该能力不新增公开 SSE 事件，不改变 Spring 对业务数据、项目文件、构建、发布和对外 SSE 的所有权。完整源码快照、Reviewer summary、`reviewer_results`、`quality_issues` 和 minor 详情都不持久化；`ai_workflow_status` 仍只保存脱敏状态摘要。LangGraph 图 checkpoint 仅在阻断问题需要恢复到 repair 时保存最多 4000 字符的 `repair_feedback`，恢复后直接进入 repair，不重复调用已经完成的 Reviewer。终态仍执行 best-effort thread 清理，TTL 继续作为异常退出兜底。本轮不启用长期记忆，也不创建或注入 `PostgresStore`。

内部工具统一契约为 `src/ai_service/contracts/internal-ai-tools-v1.json`。该文件使用 JSON Schema Draft 2020-12，同时约束工具标准名称和历史别名、模型调用权限、请求参数以及成功响应。模型只能调用 `dir_read`、`file_read`、`file_write`、`file_modify` 和 `file_delete`；`artifact_context`、`artifact_validate`、`artifact_publish`、`project_build` 和 `vue_source_snapshot` 只允许工作流调用。Spring 继续兼容已存在的 camelCase 和旧 snake_case 别名，但模型提示只使用标准名称。

Python 工作流先向 `arguments` 注入可信的 `codeGenType`，`SpringToolGateway` 再按对应 `requestSchema` 严格校验完整 `arguments`，并把可信的 `appId` 放入外层 HTTP envelope；未知工具、未知参数和额外字段都会在本地被拒绝，不会到达 Spring。`requestSchema` 只校验 `arguments`，不校验 envelope 中的 `appId`、`requestId`、`toolCallId` 和 `toolName`。Spring 成功响应中的 `data` 按对应 `responseSchema` 校验后才交给工作流。请求 Schema 使用 `additionalProperties: false` 防止协议漂移；响应 Schema 允许新增字段，以支持 Spring/Python 滚动升级。Schema 校验错误只暴露稳定的脱敏错误，不包含源码、文件路径、参数值或响应正文。Spring 生产代码仍负责应用范围、路径安全、字段语义、权限和其他业务校验，JSON Schema 不替代这些检查。

### 多文件安全发布约束

`MULTI_FILE` 模型响应必须严格包含且只包含 `index.html`、`style.css` 和 `script.js` 三个 Markdown 代码区块，顺序固定，正文不得为空，围栏外不得出现说明文本。Spring 的 `artifact_validate` 会再次解析该协议，并检查 HTML 外链、内联脚本/样式、Markdown 残留及 CSS/JavaScript 基础结构。

模型返回 `LENGTH`、`MAX_TOKENS`、`CONTENT_FILTER` 或 `CONTENT_FILTERED` 时，候选产物不会进入发布阶段。通过硬校验和质量检查后，Python 调用 Spring 的 `artifact_publish`；Spring 将文件写入不可变的 `.releases/<requestId>`，校验摘要后原子替换 `.current` 指针，并保留当前版本及最近两个成功版本。每次成功发布还会记录单调提交序号和 requestId 墓碑，实体版本被保留策略清理后也不能通过旧请求重放回滚。校验、写入或指针切换失败时，上一成功版本保持可预览、部署和下载。

仅当 `artifact_publish` 遇到连接中断、超时、Spring 5xx 或无法解析响应时，Python 才会使用相同 `toolCallId` 最多重试一次。Spring 明确返回的业务失败包括 HTTP 4xx，以及 HTTP 200 但 `BaseResponse.code != 0`；这两类结果都不会重试。Spring 的 Redis 工具幂等作用域是 `appId + requestId + toolCallId`；同一作用域只有 canonical 工具名和参数指纹都一致时才能回放成功结果，不一致会拒绝为冲突。

同一应用的生成流程由 Spring 应用级租约串行化，租约覆盖用户消息入库、模型调用、校验和发布。取消和提交通过 Redis 状态锁进行原子状态转换：取消先发生则禁止发布，提交先发生则保持成功终态；下游断开不会提前释放租约。不同应用仍可并发生成。

### HTML 安全发布与前端体验契约

`HTML` 只接受两种输入：唯一且闭合的 ` ```html ... ``` ` 代码块，或首个非空 token 为 `<!doctype html>`/`<html` 且以 `</html>` 结束的纯文档。代码块外说明、重复代码块、空响应和截断响应均拒绝；典型错误码为 `HTML_FORMAT_INVALID`、`HTML_VALIDATION_FAILED`、`HTML_DOCUMENT_INCOMPLETE`、`HTML_STYLE_INCOMPLETE`、`HTML_SCRIPT_INCOMPLETE`、`HTML_SCRIPT_TRAILING_FRAGMENT`。

Spring 在发布前执行 HTML/CSS/JavaScript 确定性扫描和 Selenium 烟测。烟测默认开启且必需（`AI_HTML_SMOKE_TEST_ENABLED=true`、`AI_HTML_SMOKE_TEST_REQUIRED=true`），浏览器不可用或页面在 8 秒内不能加载、3 秒观察期仍有错误/骨架屏时不会发布。仅本地开发可同时关闭两个开关。单文件全量重写超过 `AI_HTML_MAX_REWRITE_SOURCE_CHARS`（默认 24000）时返回 `HTML_OUTPUT_BUDGET_EXCEEDED`，避免大页面再次被截断覆盖。

HTML 和多文件版本均写入 `<类型>_<appId>/.releases/<requestId>`，`.current` 以原子替换指向活动版本；`.published` 墓碑、`.publication-sequence` 和 `.committed` 标记防止旧请求重放。默认保留当前版本及最近两个历史版本，校验、烟测、指针切换或请求失败都保留上一成功版本。

前端只在 80ms 批处理窗口更新字符计数和轻量进度，滚动最多每 200ms 一次；生成期间保留旧 iframe，只有收到发布成功的 `done` 才刷新一次。取消、业务失败（`business-error`）或截断不会刷新预览。优化提示按 `HTML`、`MULTI_FILE`、`VUE_PROJECT` 分别约束输出协议和修改范围。

## 环境要求

- Python `3.12`，项目不支持 Python 3.13
- [`uv`](https://docs.astral.sh/uv/) 包和虚拟环境管理器
- PostgreSQL 15 或更高版本；Python checkpoint 使用独立数据库 `yu_ai_checkpoint`
- DeepSeek 或其他 OpenAI 兼容模型服务的 API Key
- 可访问的 Spring Boot 后端
- Docker，可选，仅在容器运行时需要

## 配置说明

先从示例创建本地配置：

```powershell
Copy-Item .env.example .env
```

`.env` 已被忽略，不要将真实密钥提交到 Git。

| 环境变量 | 作用 | 示例或默认值 |
| --- | --- | --- |
| `AI_SERVICE_INTERNAL_BEARER_TOKEN` | Spring 调用 Python 内部接口使用的令牌 | 必须配置 |
| `AI_SERVICE_SPRING_GATEWAY_BASE_URL` | Spring 工具网关基础地址 | 容器内可使用 `http://host.docker.internal:8123/api/internal/ai-tools` |
| `AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN` | Python 调用 Spring 工具网关使用的令牌 | 必须配置，并与 Spring `ai.token` 一致 |
| `AI_SERVICE_MODEL_API_KEY` | 模型服务 API Key | 必须在真实模型调用前配置 |
| `AI_SERVICE_MODEL_BASE_URL` | OpenAI 兼容接口地址 | `https://api.deepseek.com/v1` |
| `AI_SERVICE_MODEL_NAME` | 默认聊天模型 | `deepseek-chat` |
| `AI_SERVICE_MODEL_TEMPERATURE` | 模型温度 | `0.1` |
| `AI_SERVICE_CHECKPOINT_ENABLED` | 是否启用 PostgreSQL checkpoint | `true` |
| `AI_SERVICE_CHECKPOINT_REQUIRED` | PostgreSQL 不可用时是否阻止启动或中止写入 | `false` |
| `AI_SERVICE_CHECKPOINT_POSTGRES_URL` | 独立 checkpoint 数据库连接地址 | `postgresql://<user>:<password>@localhost:5432/yu_ai_checkpoint` |
| `AI_SERVICE_CHECKPOINT_AUTO_SETUP` | 启动时是否自动初始化表；生产推荐关闭 | `true` |
| `AI_SERVICE_CHECKPOINT_TTL_SECONDS` | checkpoint 过期时间，秒 | `86400` |
| `AI_SERVICE_CHECKPOINT_POOL_MIN_SIZE` | PostgreSQL 连接池最小连接数 | `1` |
| `AI_SERVICE_CHECKPOINT_POOL_MAX_SIZE` | PostgreSQL 连接池最大连接数 | `5` |
| `AI_SERVICE_VUE_MAX_TOOL_CALLS` | 单次 Vue 工作流最大工具调用次数 | `4` |
| `AI_SERVICE_MAX_REPAIR_ATTEMPTS` | 质量检查失败后的最大修复次数 | `2` |
| `AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED` | 是否仅为 Vue 开启三角色质量审查 | `false` |
| `AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS` | 三个 Reviewer 共享的整体超时秒数 | `60` |

客服 RAG 使用 `local_cross_encoder` 时，Reranker 在独立子进程中独占指定的模型与 GPU。模型子进程通过 OS 级 ownership lock 强制同一 `model + device` 同时只有一个 owner；同机启动多个 Web worker 不会重复占用同一 GPU，而是让后启动实例以稳定 unavailable 失败。需要多 Web worker 时，应部署独立 Reranker 服务，或为每个实例分配不同 GPU，不要依赖进程内并发配置共享单卡。

`AI_SERVICE_RAG_MIN_RERANK_SCORE` 默认不设置，此时只对空检索结果执行拒答门。该阈值不得凭经验填写，必须在真实客服评估集上确定后再配置。启用阈值后，如果 Reranker 不可用，服务不会用量纲不同的 Milvus 分数替代阈值判断，而是保守返回无答案；将 `AI_SERVICE_RAG_RERANKER_PROVIDER=disabled` 视为明确的运维模式，按 Milvus 原始顺序取前三且不标记运行时降级。

本机直接启动时，通常需要将 `.env` 中的 Spring 和 PostgreSQL 地址改为：

```dotenv
AI_SERVICE_SPRING_GATEWAY_BASE_URL=http://localhost:8123/api/internal/ai-tools
AI_SERVICE_CHECKPOINT_POSTGRES_URL=postgresql://yu_ai_checkpoint:<set-outside-git>@localhost:5432/yu_ai_checkpoint
AI_SERVICE_MULTI_AGENT_REVIEW_ENABLED=false
AI_SERVICE_MULTI_AGENT_REVIEW_TIMEOUT_SECONDS=60
```

仓库不创建或管理本机已有的 PostgreSQL Docker 容器。由数据库管理员在容器中执行以下 SQL，真实密码不得写入仓库：

```sql
CREATE ROLE yu_ai_checkpoint LOGIN PASSWORD '<set-outside-git>';
CREATE DATABASE yu_ai_checkpoint OWNER yu_ai_checkpoint;
```

角色和数据库准备后，运行独立初始化命令：

```powershell
uv run python -m ai_service.infrastructure.checkpoint_setup
```

本地可保留 `AI_SERVICE_CHECKPOINT_AUTO_SETUP=true`。生产环境推荐先执行初始化命令，再以 `false` 启动并移除运行账号的 DDL 权限。

## 本地启动

以下命令均在 `ai-service` 目录执行。

### 推荐方案：使用固定的本地 Python 运行时

Windows 上如果 uv 默认管理目录中的 Python 链接损坏，可能出现依赖检查成功但 Uvicorn 无法启动的问题。推荐首次启动时将 CPython 3.12.14 安装到当前用户的 `%LOCALAPPDATA%`，避开 uv 默认的 minor-version Junction 和 trampoline。

首次初始化或重建环境前，先在运行 Uvicorn 的终端按 `Ctrl+C` 停止 AI 服务。Windows 会锁定正在使用的 `.venv` 文件；服务未停止时执行 `uv venv --clear` 会报“拒绝访问”。确认服务已停止后逐段执行：

```powershell
$runtimeDir = "$env:LOCALAPPDATA/yu-ai-code-mother/python"
uv python install 3.12.14 --install-dir "$runtimeDir" --no-bin --force
if ($LASTEXITCODE -ne 0) { throw "Python 3.12 installation failed" }

$python = (Get-ChildItem "$runtimeDir/cpython-3.12.14-windows*/python.exe" | Select-Object -First 1).FullName
if (-not $python) { throw "Python 3.12 executable not found" }

uv venv --clear --python "$python" .venv
if ($LASTEXITCODE -ne 0) { throw "Virtual environment creation failed" }

uv sync --frozen --python "$python" --link-mode copy
if ($LASTEXITCODE -ne 0) { throw "Dependency synchronization failed" }
```

初始化完成后，日常启动执行：

```powershell
$runtimeDir = "$env:LOCALAPPDATA/yu-ai-code-mother/python"
$python = (Get-ChildItem "$runtimeDir/cpython-3.12.14-windows*/python.exe" | Select-Object -First 1).FullName
if (-not $python) { throw "Python 3.12 executable not found; run the initialization steps first" }

$env:PYTHONPATH = "$(Resolve-Path './.venv/Lib/site-packages');$(Resolve-Path './src')"
& "$python" -m uvicorn ai_service.app:create_app --factory --host 0.0.0.0 --port 8000
```

### 简化方案

如果 uv 管理的 Python 链接工作正常，可继续使用：

```powershell
uv sync --frozen --python 3.12
uv run uvicorn ai_service.app:create_app --factory --host 0.0.0.0 --port 8000
```

如果出现 `No Python at ...`，路径前带有异常引号，或 uv 报告 `Missing expected target directory for Python minor version link`，不要重复执行简化方案，改用上面的推荐方案重新初始化。模块名必须写成 `ai_service.app:create_app`，不要在下划线前添加反斜杠。

服务默认监听 `http://localhost:8000`。

Spring Boot 侧至少配置：

```powershell
$env:AI_ENGINE = "langgraph"
$env:AI_SERVICE_URL = "http://localhost:8000"
$env:AI_SERVICE_INTERNAL_BEARER_TOKEN = "与Python服务一致的内部令牌"
# Spring 内部工具幂等记录的保留时间，单位为秒
$env:AI_TOOL_IDEMPOTENCY_TTL_SECONDS = "86400"
# Spring 等待同一工具调用分布式锁的最长时间，单位为毫秒
$env:AI_TOOL_IDEMPOTENCY_LOCK_WAIT_MILLIS = "30000"
```

后两项是启动 Spring 时注入的可选运行时环境变量，不属于 `ai-service/.env` 的必需配置。

随后在仓库根目录启动 Spring Boot：

```powershell
.\mvnw.cmd spring-boot:run
```

## Docker 启动

确保 Docker daemon 已运行，在 `ai-service` 目录执行：

```powershell
docker build -t yu-ai-service:local .
docker run --rm --name yu-ai-service -p 8000:8000 --env-file .env yu-ai-service:local
```

容器访问宿主机 Spring 和 PostgreSQL 时可使用：

```dotenv
AI_SERVICE_SPRING_GATEWAY_BASE_URL=http://host.docker.internal:8123/api/internal/ai-tools
AI_SERVICE_CHECKPOINT_POSTGRES_URL=postgresql://yu_ai_checkpoint:replace-with-password@host.docker.internal:5432/yu_ai_checkpoint
```

Linux 环境如果无法解析 `host.docker.internal`，需要改为宿主机可达地址或将服务放入同一个 Docker network。

## 健康检查

健康检查无需 Bearer 令牌：

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

- `live` 只表示 Python 进程存活。
- `ready` 会检查 checkpoint 存储状态；依赖不可用时返回 HTTP 503。
- 同时兼容 `/internal/v1/health/live` 和 `/internal/v1/health/ready`。

## 内部接口

除健康检查外，接口都要求：

```http
Authorization: Bearer <AI_SERVICE_INTERNAL_BEARER_TOKEN>
```

| 方法与路径 | 用途 |
| --- | --- |
| `POST /internal/v1/route` | 将提示词分类为 `HTML`、`MULTI_FILE` 或 `VUE_PROJECT` |
| `POST /internal/v1/generations:stream` | 启动工作流并返回 `application/x-ndjson` 事件流 |
| `POST /internal/v1/generations/{requestId}:cancel` | 协作式取消指定请求 |
| `GET /health/live` | 进程存活检查 |
| `GET /health/ready` | PostgreSQL checkpoint 就绪检查 |

生成事件包含 `requestId`、递增的 `sequence`、`node`、`data` 和可选的 `error`。事件类型包括：

- `content_delta`
- `tool_started`
- `tool_finished`
- `node_status`
- `completed`
- `failed`

Python 与 Spring 之间使用 NDJSON；Spring 对前端的内容事件仍为 `data: {"d":"..."}`。只有产物成功发布后才发送命名 `done` 事件；模型截断、校验失败或发布失败会发送一个命名 `error` 事件，其中包含数字 `code`、稳定的 `errorCode`、`message` 和 `requestId`，且不会再发送 `done`。

`completed.data` 只携带生成类型、质量、修复/工具计数以及发布或构建摘要，不重复携带完整源码。HTML/MULTI_FILE 的最终候选仍来自最后一个 `content_delta`，且只有 Spring 发布成功后 Java 才向现有下游提交该候选。

业务 checkpoint 不保存完整源码或审查正文，`ai_workflow_status` 只保留 `node`、`requestId`、`appId`、`codeGenType`、`qualityPassed`、`repairCount` 和 `toolCallCount` 等状态与审计摘要。执行期节点恢复由 LangGraph 自动 checkpoint 承担，该 checkpoint 在请求执行期间仍可能包含完整产物；Vue 多 Agent 审查只额外保留最多 4000 字符的阻断性 `repair_feedback`，不保存 minor 详情、Reviewer summary、`reviewer_results` 或 `quality_issues`。从已完成的 `quality_review` 恢复时直接进入 repair，不重复执行 Reviewer。请求在成功、失败或取消进入终态后会立即 best-effort 清理对应 thread，TTL 仅作为异常退出时的兜底。若终态清理失败，只会使 checkpoint 就绪状态降级，不会反转已经确定的生成结果。

## PostgreSQL checkpoint 与故障降级

LangGraph 使用官方 `AsyncPostgresSaver`，`thread_id` 为 `{appId}:{requestId}`。同一连接池还维护 `ai_workflow_status`，该表只允许保存节点、请求/应用标识、生成类型、质量结果和有限计数，不保存完整源码、`repair_feedback` 或其他审查正文。正常终态调用 `adelete_thread()` 删除官方图 checkpoint；异常退出先由 `expires_at` 找到过期 thread，成功删除图 checkpoint 后才删除状态行，图删除失败时保留状态行供下次重试。

- `AI_SERVICE_CHECKPOINT_REQUIRED=false`：PostgreSQL 不可用时记录脱敏警告，服务继续运行但不具备 checkpoint 恢复能力，ready 返回 503。
- `AI_SERVICE_CHECKPOINT_REQUIRED=true`：启动探测或 checkpoint 写入失败时显式失败。
- `AI_SERVICE_CHECKPOINT_ENABLED=false`：完全关闭 checkpoint，仅建议用于测试或明确接受无恢复能力的本地环境。
- `AI_SERVICE_CHECKPOINT_AUTO_SETUP=true`：启动时幂等初始化官方表和 `ai_workflow_status`，适合本地；生产推荐独立初始化后关闭。

序列化保持 pickle fallback 关闭，并通过 `allowed_json_modules=()` 不额外允许自定义 JSON constructor 模块。锁定依赖版本不存在 `LANGGRAPH_STRICT_MSGPACK` 配置开关，因此数据库必须只允许可信 AI 服务写入，不能把无效环境变量当作安全边界。

本轮不启用 `PostgresStore` 长期记忆。Spring 聊天历史仍是跨请求对话上下文；只有出现明确的跨应用偏好或跨 requestId 结构化决策，并定义查看、修改、删除和过期规则后才重新评估。

Spring 内部工具幂等使用现有 Spring Redis 配置（本地默认 database 1），因此幂等状态和成功结果可跨 Spring 实例共享。作用域为 `appId + requestId + toolCallId`；同一作用域复用不同 canonical 工具名或参数指纹会被拒绝。已存在（包括陈旧）的 `RUNNING` 记录表示执行结果不确定，不会自动重放；action 已成功但完成状态写回 Redis 失败时同样返回 indeterminate。该机制不承诺文件系统与 Redis 之间的严格 exactly-once。

工具调用 Redis 幂等与 `VersionedArtifactStore` 的不可变版本发布幂等是两套独立机制，不能相互替代。本轮单元和契约验证不包含真实 Redis 多 Spring 实例场景，该场景仍需集成测试验证。

## 稳定灰度与真实验收入口

Spring 灰度路由使用 `graySalt` 和稳定主体（`userId` -> `appId` -> `requestId`）计算 SHA-256 桶，`route`、`generate`、`cancel` 会选择同一引擎。仓库提供 `scripts/compare-ai-generation-engines.ps1`、`scripts/test-ai-service.ps1` 和 `scripts/test-ai-phase-two-e2e.ps1`，脚本默认只显示检查清单，必须显式使用 `-Execute` 和隔离测试应用 ID 才会发送请求。

真实 Redis 多实例测试通过以下命令 opt-in：

```powershell
$env:AI_REDIS_INTEGRATION = "true"
$env:AI_REDIS_URL = "redis://127.0.0.1:6379/1"
mvn "-Dtest=ToolInvocationIdempotencyRedisIT" test
```

真实 PostgreSQL checkpoint 测试通过以下命令 opt-in，连接 URL 只放在当前进程环境中，不得打印或提交：

```powershell
$env:AI_SERVICE_POSTGRES_INTEGRATION = "true"
$env:AI_SERVICE_CHECKPOINT_POSTGRES_URL = "postgresql://<user>:<password>@localhost:5432/yu_ai_checkpoint"
uv run pytest tests/test_postgres_checkpoint_integration.py
```

默认不设置 `AI_SERVICE_POSTGRES_INTEGRATION` 时，该测试跳过且不连接数据库。当前本机 Docker 容器中尚未创建独立 `yu_ai_checkpoint` 数据库，因此本轮不声称真实 PostgreSQL 集成通过；真实 Spring/Python HTTP、真实模型和三类型端到端结果仍需在隔离环境中记录。

## 测试与校验

测试使用 Fake Model 和内存工具网关，不调用真实模型：

```powershell
uv run pytest
```

提交前建议运行完整校验：

```powershell
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

仓库根目录的统一门禁会执行上述 Python 检查；只有独立 checkpoint 数据库准备完毕时才追加 `-IncludePostgres`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1 -Execute
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/verify-langgraph-real-gate.ps1 -Execute -IncludePostgres
```

## 安全要求

- 不得提交 `.env`、API Key、Bearer 令牌或生产服务地址。
- 生产环境应通过密钥管理系统或运行时环境变量注入凭据。
- Python 服务应部署在受控内网，不直接暴露给浏览器或公网客户端。
- Python 不挂载或直接操作项目目录，文件和构建操作必须经过 Spring 工具网关。
- 日志默认不记录完整提示词、生成源码、工具参数和密钥。
- 仓库历史中曾暴露的凭据必须轮换，不能仅从当前配置文件删除。

## 相关文档

- [AI 服务详细启动说明](../doc/ai-service-startup.md)
- [LangChain + LangGraph 重构方案](../doc/ai-service-langchain-langgraph-refactor-design.md)
