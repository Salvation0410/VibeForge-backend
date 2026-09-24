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
                -> Redis checkpoint
```

| 组件 | 主要职责 |
| --- | --- |
| Spring Boot | 用户鉴权、应用权限、聊天记录、文件操作、项目构建、部署、下载和对外 SSE |
| Python AI 服务 | 模型调用、生成类型路由、LangGraph 编排、质量检查、修复和工具调用决策 |
| Redis | 保存 LangGraph checkpoint 和本次编排状态，默认 TTL 为 24 小时 |
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
│       ├── checkpoint.py       # Redis checkpoint 和 LangGraph saver
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

Vue 至少完成一次修复、重新通过硬校验并重新构建成功后，质量检查会调用 Spring 工作流专用且模型不可调用的 `vue_source_snapshot`，以最终项目源码而不是旧 artifact 作为当前 Reviewer 的审查输入。首次未发生修复的 Vue，以及 HTML、MULTI_FILE 分支均不调用该工具。快照最多返回 24 个文件，单文件内容最多 12000 个字符，总内容最多 60000 个字符；Spring 遍历的项目总访问条目（根目录之外的目录、文件和访问失败条目）最多 20000 个，其中合格源码候选最多 10000 个，并只读取按 `package.json`、入口文件、`src/App.vue`、其余路径稳定排序后的最佳 24 个候选，每个源文件最大 1 MiB。

快照排除依赖和构建产物目录、隐藏目录、符号链接、锁文件及非文本扩展名，依赖/构建目录和锁文件的大小写变体同样排除；入选文件执行严格 UTF-8 与 NUL 检查。完整快照只在本次质量检查调用栈内传给当前 Reviewer，不写入 Spring 工具幂等 Redis、业务 checkpoint、LangGraph state/checkpoint 或 NDJSON 事件；质量检查失败时可能产生的 pending checkpoint 也只包含稳定的外层异常，不包含源码。`tool_finished` 事件只公开 `eligibleFileCount`、`includedFileCount`、`omittedFileCount` 和 `truncated` 四个统计字段。快照读取失败使用稳定的脱敏消息，错误响应不包含绝对项目路径；快照读取或 Reviewer 调用失败时不回退到旧 artifact，而是进入失败终态。

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
- Redis 5 或更高版本
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
| `AI_SERVICE_REDIS_ENABLED` | 是否启用 Redis checkpoint | `true` |
| `AI_SERVICE_REDIS_REQUIRED` | Redis 不可用时是否阻止启动或中止写入 | `false` |
| `AI_SERVICE_REDIS_URL` | Redis 连接地址 | 本地可使用 `redis://localhost:6379/2` |
| `AI_SERVICE_CHECKPOINT_TTL_SECONDS` | checkpoint 过期时间，秒 | `86400` |
| `AI_SERVICE_VUE_MAX_TOOL_CALLS` | 单次 Vue 工作流最大工具调用次数 | `4` |
| `AI_SERVICE_MAX_REPAIR_ATTEMPTS` | 质量检查失败后的最大修复次数 | `2` |

本机直接启动时，通常需要将 `.env` 中的 Spring 和 Redis 地址改为：

```dotenv
AI_SERVICE_SPRING_GATEWAY_BASE_URL=http://localhost:8123/api/internal/ai-tools
AI_SERVICE_REDIS_URL=redis://localhost:6379/2
```

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

容器访问宿主机 Spring 和 Redis 时可使用：

```dotenv
AI_SERVICE_SPRING_GATEWAY_BASE_URL=http://host.docker.internal:8123/api/internal/ai-tools
AI_SERVICE_REDIS_URL=redis://host.docker.internal:6379/2
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
| `GET /health/ready` | Redis checkpoint 就绪检查 |

生成事件包含 `requestId`、递增的 `sequence`、`node`、`data` 和可选的 `error`。事件类型包括：

- `content_delta`
- `tool_started`
- `tool_finished`
- `node_status`
- `completed`
- `failed`

Python 与 Spring 之间使用 NDJSON；Spring 对前端的内容事件仍为 `data: {"d":"..."}`。只有产物成功发布后才发送命名 `done` 事件；模型截断、校验失败或发布失败会发送一个命名 `error` 事件，其中包含数字 `code`、稳定的 `errorCode`、`message` 和 `requestId`，且不会再发送 `done`。

`completed.data` 只携带生成类型、质量、修复/工具计数以及发布或构建摘要，不重复携带完整源码。HTML/MULTI_FILE 的最终候选仍来自最后一个 `content_delta`，且只有 Spring 发布成功后 Java 才向现有下游提交该候选。

业务 checkpoint 不保存完整源码，业务快照保留 `node`、`requestId`、`appId`、`codeGenType`、`qualityPassed`、`repairCount` 和 `toolCallCount` 等状态与审计摘要；执行期节点恢复由 LangGraph 自动 checkpoint 承担，该 checkpoint 在请求执行期间仍可能包含完整产物。请求在成功、失败或取消进入终态后会立即清理对应 thread，TTL 仅作为异常退出时的兜底。若终态清理失败，只会使 checkpoint 就绪状态降级，不会反转已经确定的生成结果。

## Redis 与故障降级

Redis key 使用 `yu-ai:langgraph:*` 命名空间，LangGraph `thread_id` 为 `{appId}:{requestId}`，所有 key 默认在 24 小时后过期。

- `AI_SERVICE_REDIS_REQUIRED=false`：Redis 不可用时记录警告，服务继续运行但不具备 checkpoint 恢复能力，ready 返回 503。
- `AI_SERVICE_REDIS_REQUIRED=true`：启动探测或 checkpoint 写入失败时显式失败。
- `AI_SERVICE_REDIS_ENABLED=false`：完全关闭 checkpoint，仅建议用于测试或明确接受无恢复能力的本地环境。

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

本轮没有执行真实 Spring/Python HTTP、真实 Redis、真实模型或三类型端到端验收；这些结果必须由下一阶段在隔离环境中补充记录。

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
