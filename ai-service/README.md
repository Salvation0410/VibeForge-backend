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

### 多文件安全发布约束

`MULTI_FILE` 模型响应必须严格包含且只包含 `index.html`、`style.css` 和 `script.js` 三个 Markdown 代码区块，顺序固定，正文不得为空，围栏外不得出现说明文本。Spring 的 `artifact_validate` 会再次解析该协议，并检查 HTML 外链、内联脚本/样式、Markdown 残留及 CSS/JavaScript 基础结构。

模型返回 `LENGTH`、`MAX_TOKENS`、`CONTENT_FILTER` 或 `CONTENT_FILTERED` 时，候选产物不会进入发布阶段。通过硬校验和质量检查后，Python 调用 Spring 的 `artifact_publish`；Spring 将文件写入不可变的 `.releases/<requestId>`，校验摘要后原子替换 `.current` 指针，并保留当前版本及最近两个成功版本。每次成功发布还会记录单调提交序号和 requestId 墓碑，实体版本被保留策略清理后也不能通过旧请求重放回滚。校验、写入或指针切换失败时，上一成功版本保持可预览、部署和下载。

`artifact_publish` 遇到连接中断、超时、Spring 5xx 或无法解析响应时，会使用相同 `toolCallId` 最多重试一次。Spring 的工具幂等缓存与 requestId/hash 校验保证重试不会创建不同版本；明确的 4xx 业务拒绝不会重试。

同一应用的生成流程由 Spring 应用级租约串行化，租约覆盖用户消息入库、模型调用、校验和发布。取消和提交通过 Redis 状态锁进行原子状态转换：取消先发生则禁止发布，提交先发生则保持成功终态；下游断开不会提前释放租约。不同应用仍可并发生成。

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
```

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

## Redis 与故障降级

Redis key 使用 `yu-ai:langgraph:*` 命名空间，LangGraph `thread_id` 为 `{appId}:{requestId}`，所有 key 默认在 24 小时后过期。

- `AI_SERVICE_REDIS_REQUIRED=false`：Redis 不可用时记录警告，服务继续运行但不具备 checkpoint 恢复能力，ready 返回 503。
- `AI_SERVICE_REDIS_REQUIRED=true`：启动探测或 checkpoint 写入失败时显式失败。
- `AI_SERVICE_REDIS_ENABLED=false`：完全关闭 checkpoint，仅建议用于测试或明确接受无恢复能力的本地环境。

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
