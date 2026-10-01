# AI 服务启动说明

本文说明 `ai-service` 独立 LangChain + LangGraph 服务的本地启动、Docker 启动及与 Spring Boot 的联调方式。

## 源码目录说明

AI 服务源码位于 `ai-service/src/ai_service`，按职责分为以下目录：

```text
ai_service/
├── app.py              # 应用工厂和依赖组装入口
├── config.py           # 环境变量与服务配置
├── api/                # HTTP 路由、鉴权依赖、请求响应模型
├── orchestration/      # LangGraph 工作流、事件流和取消控制
├── models/             # 模型协议与 OpenAI 兼容模型实现
└── infrastructure/     # PostgreSQL checkpoint、初始化命令与 Spring 工具网关
```

`app.py` 是组合入口，启动命令仍使用 `ai_service.app:create_app`。Python 服务不直接访问项目目录，文件和构建操作统一经过 `infrastructure/spring_tools.py` 调用 Spring。

## 1. 前置条件

- Python 3.12（必须使用 3.12，不建议使用 3.13）
- `uv` 包管理器
- PostgreSQL 15+（Python checkpoint 使用独立数据库 `yu_ai_checkpoint`；本地可由 Docker 提供）
- DeepSeek 或其他 OpenAI 兼容模型服务的 API Key
- Spring Boot 后端已配置并可访问项目输出目录

安装 `uv`：

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 2. 本地启动

在仓库根目录执行：

```powershell
cd ai-service
Copy-Item .env.example .env
notepad .env
uv sync --frozen --python 3.12
uv run uvicorn ai_service.app:create_app --factory --host 0.0.0.0 --port 8000
```

编辑 `.env` 时至少替换以下配置：

```dotenv
AI_SERVICE_INTERNAL_BEARER_TOKEN=与Spring配置一致的服务令牌
AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN=与Spring配置一致的工具令牌
AI_SERVICE_MODEL_API_KEY=模型服务API Key
AI_SERVICE_CHECKPOINT_POSTGRES_URL=postgresql://yu_ai_checkpoint:<set-outside-git>@localhost:5432/yu_ai_checkpoint
```

默认模型为 `deepseek-chat`，默认地址为 `https://api.deepseek.com/v1`。复杂推理节点可通过配置改用兼容服务提供的推理模型。

Python checkpoint 使用独立数据库 `yu_ai_checkpoint`，不复用 Spring 业务 MySQL。当前仓库不创建或管理本机已有的 PostgreSQL Docker 容器；由本机数据库管理员在容器中执行以下 SQL，密码只保存在本地环境或密钥管理中：

```sql
CREATE ROLE yu_ai_checkpoint LOGIN PASSWORD '<set-outside-git>';
CREATE DATABASE yu_ai_checkpoint OWNER yu_ai_checkpoint;
```

数据库和角色准备后，先初始化官方 LangGraph 表及脱敏状态表：

```powershell
cd ai-service
uv run python -m ai_service.infrastructure.checkpoint_setup
```

`AI_SERVICE_CHECKPOINT_AUTO_SETUP=true` 适合本地开发。生产环境推荐先使用初始化命令完成 DDL，再将其设为 `false`，并让运行账号只保留所需的数据访问权限。当前不启用 `PostgresStore` 长期记忆；跨请求上下文仍由 Spring 聊天历史提供。

## 3. 启动 Spring Boot

Spring Boot 需要指向 Python 服务，并配置相同的内部令牌：

```powershell
$env:AI_ENGINE = "langgraph"
$env:AI_SERVICE_URL = "http://localhost:8000"
$env:AI_SERVICE_INTERNAL_BEARER_TOKEN = "与Python一致的服务令牌"
# Spring 内部工具幂等记录的保留时间，单位为秒
$env:AI_TOOL_IDEMPOTENCY_TTL_SECONDS = "86400"
# Spring 等待同一工具调用分布式锁的最长时间，单位为毫秒
$env:AI_TOOL_IDEMPOTENCY_LOCK_WAIT_MILLIS = "30000"
```

后两项是 Spring 的可选运行时环境变量，不需要写入 `ai-service/.env`。

随后在仓库根目录启动：

```powershell
.\mvnw.cmd spring-boot:run
```

灰度发布时可使用：

```yaml
ai:
  engine: gray
  gray-whitelist: [10001, 10002]
  gray-percentage: 10
```

`legacy` 为原 LangChain4j 链路，`langgraph` 为 Python 链路，`gray` 按白名单和比例选择。切换配置即可回滚，不需要数据库迁移。

两条生成链路共用 Spring 的不可变安全发布边界：同一应用同一时间只允许一个生成请求；取消和提交通过 Redis 状态锁决定唯一胜者；模型输出因长度或内容策略中止时不发布。`HTML` 只接受唯一闭合代码块或边界完整纯文档，发布前执行确定性 HTML/CSS/JavaScript 校验和 Selenium 烟测；`MULTI_FILE` 必须是严格的三文件协议。成功版本位于 `<类型>_<appId>/.releases/<requestId>`，`.current` 原子指向当前版本，预览、部署、下载和导出都读取该版本。默认保留当前版本及最近两个成功版本，同时持久记录单调提交序号、requestId 墓碑和提交标记；失败请求或已归档旧请求不会覆盖上一成功版本。

HTML 烟测默认由 `AI_HTML_SMOKE_TEST_ENABLED=true` 和 `AI_HTML_SMOKE_TEST_REQUIRED=true` 开启；浏览器不可用、加载超时、脚本错误或骨架屏未消失都会拒绝发布。`AI_HTML_MAX_REWRITE_SOURCE_CHARS` 默认 24000，超过预算的全量重写返回 `HTML_OUTPUT_BUDGET_EXCEEDED`。前端生成期间保留旧预览，进度按 80ms 批处理，成功 `done` 后只刷新一次；`business-error`、取消和截断不会刷新预览。

## 4. 健康检查

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

也兼容 `/internal/v1/health/live` 和 `/internal/v1/health/ready`。ready 检查会反映 PostgreSQL checkpoint 状态；数据库不可用时返回 503，`AI_SERVICE_CHECKPOINT_REQUIRED=true` 时启动探测或运行期写入失败会显式失败。

客服 RAG 使用独立且需要 Bearer 鉴权的健康端点，不参与代码生成 readiness：

```powershell
$headers = @{ Authorization = "Bearer $env:AI_SERVICE_INTERNAL_BEARER_TOKEN" }
Invoke-RestMethod http://localhost:8000/internal/v1/customer-service/health -Headers $headers
```

关闭 `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED` 时响应为 `disabled`，且不会导入、初始化或连接 Milvus、CloseAI、文档 ETL 与 GPU Reranker。开启后，任何依赖未知异常、超时或 false 状态都 fail-safe 为 `degraded` 和 HTTP 503；摘要只有稳定状态与布尔值，不包含连接 URI、密钥或异常正文。即使客服 degraded，`/health/ready` 仍只由 checkpoint 决定。

### 4.1 客服 RAG 启动顺序

1. 启动 MySQL、Redis 和私有 OSS，并完成 Spring 知识表迁移；真实凭据只放在部署环境。
2. 启动 Spring，确认内部 lease health、管理员知识接口和 outbox worker 可用。Python 与 Spring 两个方向当前使用部署约定的共享 token，但示例和文档不得写真实值。
3. 启动 Docker Milvus，确认数据库和 `AI_SERVICE_MILVUS_COLLECTION_ALIAS`。alias 是稳定读入口，物理 collection 是不可变版本；默认 retention 为 2 代。
4. 配置 CloseAI Base URL/API Key、OSS HTTPS 白名单，以及 GPU Reranker model/device。`local_cross_encoder` 对同一 model+device 只允许一个 owner；多 Uvicorn worker 不能共享同一卡上的本地模型。
5. 先保持客服开关关闭启动 Python，检查 `/health/ready`；再开启开关并滚动重启，检查认证客服 health。
6. 使用管理员页面 `/admin/customer-service/knowledge` 上传 PDF/DOCX/MD/TXT。文件经 Spring 私有 OSS 和 outbox 进入 ETL；任务按 `PENDING -> RUNNING -> SUCCEEDED` 推进，可重试失败回到 `PENDING`，耗尽重试进入 `FAILED`。观察 `documentId/documentVersion/etlVersion/status/errorCode`，不要记录签名 URL 或 lease proof。
7. ETL 成功后执行 synthetic smoke、no-answer、prompt-injection 与引用核对；真实环境结果需单独保存脱敏证据。

### 4.2 错误、版本与回滚

- ETL/DELETE/REBUILD 只按稳定 `error.code` 处理；常见类别为 `KNOWLEDGE_DOWNLOAD_*`、`KNOWLEDGE_DOCUMENT_*`、`KNOWLEDGE_EMBEDDING_*`、`KNOWLEDGE_MUTATION_LEASE_*`、`KNOWLEDGE_VECTOR_*` 和 `KNOWLEDGE_REBUILD_*`。HTTP 503/未知异常不能用供应商正文替代错误码。失败、超时或状态不确定时保留上一活动 alias。
- rebuild 在 staging collection 完整写入和校验后才切 alias。默认保留当前和一个回滚版本；清理需要 retirement marker、alias ownership、grace window 和 scan budget 同时满足。禁止手工删除 current、protected 或 ownership 不明的 collection。
- 客服故障优先设置 `AI_SERVICE_CUSTOMER_SERVICE_RAG_ENABLED=false` 并滚动重启 Python；确认代码生成 ready 正常。需要数据回滚时，仅将 alias 指向已经人工核验、模型和维度兼容的上一物理版本。
- 长期记忆/`PostgresStore` 仍关闭；客服不使用 LangGraph checkpoint，不需要为了客服回滚修改或清理 checkpoint 表。

## 5. Docker 启动

确保 Docker daemon 已启动，在 `ai-service` 目录执行：

```powershell
docker build -t yu-ai-service:local .
docker run --rm --name yu-ai-service -p 8000:8000 --env-file .env yu-ai-service:local
```

容器内访问宿主机 Spring 和 PostgreSQL 时，`.env` 中的地址使用：

```dotenv
AI_SERVICE_SPRING_GATEWAY_BASE_URL=http://host.docker.internal:8123/api/internal/ai-tools
AI_SERVICE_CHECKPOINT_POSTGRES_URL=postgresql://yu_ai_checkpoint:replace-with-password@host.docker.internal:5432/yu_ai_checkpoint
```

Linux 环境如不支持 `host.docker.internal`，请改为宿主机可达地址或使用 Docker network。

## 6. 接口冒烟

受保护接口都需要 Bearer 令牌：

```powershell
$headers = @{ Authorization = "Bearer $env:AI_SERVICE_INTERNAL_BEARER_TOKEN" }
$body = @{ requestId = "smoke-001"; appId = "1"; prompt = "生成一个简单的产品介绍页" } | ConvertTo-Json
Invoke-RestMethod http://localhost:8000/internal/v1/route -Method Post -Headers $headers -ContentType "application/json" -Body $body
```

完整生成建议通过现有对外接口验证：

```text
GET http://localhost:8123/api/apps/chat/gen/code?appId={应用ID}&message={生成提示}
```

前端内容事件仍为 `data: {"d":"..."}`，Python 与 Spring 之间使用 `application/x-ndjson`。正常完成时 Spring 发送命名 `done` 事件；截断、校验失败或发布失败时只发送一个命名 `error` 事件，数据包含 `code`、`errorCode`、`message` 和 `requestId`，不会再发送 `done`。

LangGraph 的多文件分支会依次调用 Spring 工具 `artifact_validate` 和 `artifact_publish`。前者返回结构化校验错误供最多两次修复使用，后者在 Spring 侧重新校验并提交不可变版本。`MULTI_FILE` 不执行 Vue 项目构建，只有 `VUE_PROJECT` 进入 `project_build`。

仅当 `artifact_publish` 因连接中断、超时、Spring 5xx 或响应解析异常而无法确认结果时，Python 才会携带原 `toolCallId` 最多重试一次。Spring 明确返回的业务失败包括 HTTP 4xx，以及 HTTP 200 但 `BaseResponse.code != 0`；这两类结果都不会重试。重试耗尽表示内部网络持续不可用，应结合 Spring 日志与 `.current` 指针排查实际发布状态。

Spring 工具幂等状态和成功结果保存在现有 Spring Redis 配置中（本地默认 database 1），可跨 Spring 实例共享。幂等作用域为 `appId + requestId + toolCallId`；同一作用域对应不同 canonical 工具名或参数指纹时会拒绝执行。已存在（包括陈旧）的 `RUNNING` 记录属于不确定状态，不会自动重放；action 已成功但完成状态写回 Redis 失败时也会返回 indeterminate，因此该机制不承诺文件系统与 Redis 之间严格 exactly-once。

工具调用 Redis 幂等和 `VersionedArtifactStore` 的不可变版本发布幂等彼此独立。前者控制 Spring 工具调用的去重与冲突，后者控制产物 release、墓碑和活动指针。本轮没有运行真实 Redis 多 Spring 实例集成验证，上线前仍需覆盖该场景。

## 7. 测试与排查

### 客服离线评估

版本化 fixture 位于 `ai-service/tests/fixtures/customer_service_eval.json`，schema 为 `customer-service-rag-eval/v1`。它只包含 synthetic/non-sensitive 问题，并显式标出 expected document IDs 与 `expectedAnswerable`。评估 helper 位于 `ai_service.orchestration.customer_service_evaluation`，必须注入 fake/recorded runner；默认测试不连接 OSS、CloseAI、Milvus 或 GPU。

指标为 Recall@8、MRR@3、NDCG@3、no-answer accuracy、citation validity，以及 count/min/p50/p95/max/mean latency。检索 ID 先按首次出现去重；重复或未知 citation 计为无效；0 样本返回 0。真实阈值只能在经审批的业务评估集和真实依赖环境中确定，synthetic fixture 不能用于设定生产 `RAG_MIN_RERANK_SCORE`。

截至 2026-10-01，真实 OSS、CloseAI、Docker Milvus、GPU 模型加载/显存/吞吐、浏览器与 Spring-Python E2E 均未在本轮执行，状态保持 pending。

### 稳定灰度与第二阶段验收

灰度模式使用 `graySalt + 稳定业务主体` 计算 SHA-256 桶：优先使用 `userId`，其次使用 `appId`，最后使用 `requestId`。`route`、`generate` 和 `cancel` 使用同一选择逻辑。不要仅凭配置存在提高灰度比例。

本轮提供默认 dry-run 的验收入口。真实执行必须明确传入测试应用、测试账号和 `-Execute`，并且不得使用事故应用或生产应用：

```powershell
.\scripts\compare-ai-generation-engines.ps1 -LegacyAppId 101 -LangGraphAppId 102 -Execute
.\scripts\test-ai-service.ps1 -ReadOnlyAppId 101 -Execute
.\scripts\test-ai-phase-two-e2e.ps1 -HtmlAppId 101 -MultiFileAppId 102 -VueAppId 103 -Execute
```

真实 Redis 幂等验收为 opt-in，不设置变量时测试必须跳过：

```powershell
$env:AI_REDIS_INTEGRATION = "true"
$env:AI_REDIS_URL = "redis://127.0.0.1:6379/1"
mvn "-Dtest=ToolInvocationIdempotencyRedisIT" test
```

真实 PostgreSQL checkpoint 集成测试同样为 opt-in。先创建独立数据库并在当前进程配置连接 URL；命令本身不得打印 URL：

```powershell
$env:AI_SERVICE_POSTGRES_INTEGRATION = "true"
$env:AI_SERVICE_CHECKPOINT_POSTGRES_URL = "postgresql://<user>:<password>@localhost:5432/yu_ai_checkpoint"
cd ai-service
uv run pytest tests/test_postgres_checkpoint_integration.py
```

不设置 `AI_SERVICE_POSTGRES_INTEGRATION=true` 时，该测试会跳过且不会连接数据库。本轮已实现 PostgreSQL checkpoint 代码、离线测试和验收入口；独立数据库尚未创建，因此没有执行真实 PostgreSQL 集成、真实模型或三类型端到端生成。Spring Redis database 1 的工具幂等不在本次迁移范围内。

```powershell
cd ai-service
uv run pytest
uv lock --check
```

常见问题：

- `401 Invalid internal bearer token`：检查 Python 的 `AI_SERVICE_INTERNAL_BEARER_TOKEN` 与 Spring `AI_SERVICE_INTERNAL_BEARER_TOKEN` 是否完全一致。
- ready 返回 503：检查 PostgreSQL 地址、端口、数据库、账号权限和 `AI_SERVICE_CHECKPOINT_REQUIRED` 配置。
- 工具调用失败：确认 Python 的 Spring 网关地址包含 `/api/internal/ai-tools`，且工具令牌与 Spring `ai.token` 一致。
- `GENERATION_IN_PROGRESS`：同一应用已有生成任务，等待当前请求完成或取消后重试。
- `MODEL_OUTPUT_TRUNCATED`：模型达到 token 上限，本次候选未发布；可缩小需求或提高模型输出上限后重试。
- `MULTI_FILE_FORMAT_INVALID` / `ARTIFACT_PUBLISH_FAILED`：三文件协议、确定性校验或版本提交失败，上一成功版本仍保持活动状态。
- 模型调用失败：检查 API Key、Base URL、模型名称和网络连通性；不要把密钥写入 Git。
- Docker 无法构建：先启动 Docker daemon，再执行 `docker build`。

## 8. 安全要求

`.env` 仅用于本机或部署环境，不得提交。生产环境必须使用密钥管理系统或运行时环境变量，并轮换历史中曾暴露的 AI、OSS、邮件凭据。Python 服务不应直接挂载项目目录，所有文件和构建操作必须经过 Spring 工具网关。

## 9. HTML 人工恢复

只有管理员可以调用 `POST /api/apps/admin/artifacts/html/recover`。接口不会自动搜索聊天历史；操作员必须先从可信来源导出并人工审核一份完整候选，提供新的 `requestId` 和来源说明。默认 `dryRun=true`，只执行严格解析、确定性校验和浏览器烟测，不创建 release，也不改变 `.current`。

推荐使用 PowerShell 包装脚本，并先执行 dry-run：

```powershell
$session = New-Object Microsoft.PowerShell.Commands.WebRequestSession
# 先通过项目登录接口让 $session 获得管理员会话，再运行只读校验。
.\scripts\restore-html-release.ps1 -AppId 123 -CandidateFile C:\recovery\candidate.html `
  -RequestId recovery-20260920-001 -WebSession $session
```

确认响应中 `valid=true`、烟测通过且候选来源无误后，才用相同参数显式提交：

```powershell
.\scripts\restore-html-release.ps1 -AppId 123 -CandidateFile C:\recovery\candidate.html `
  -RequestId recovery-20260920-001 -WebSession $session -Commit
```

`-Commit` 会以 `manual-recovery` 引擎发布新的不可变 HTML release，并与在线生成共用应用级互斥和发布门禁。脚本不包含凭据或 Cookie，只使用调用方传入的 `WebRequestSession`；它只读取 `CandidateFile`，不会搜索历史目录，也不会读取或修改 `projects/`。不得把损坏的平铺 `index.html` 直接作为候选，无法找到完整候选时应保留现场并从原始需求重新生成。
