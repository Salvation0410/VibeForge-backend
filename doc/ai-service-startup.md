# AI 服务启动说明

本文说明 `ai-service` 独立 LangChain + LangGraph 服务的本地启动、Docker 启动及与 Spring Boot 的联调方式。

## 1. 前置条件

- Python 3.12（必须使用 3.12，不建议使用 3.13）
- `uv` 包管理器
- Redis 5+（默认连接 database 2）
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
```

默认模型为 `deepseek-chat`，默认地址为 `https://api.deepseek.com/v1`。复杂推理节点可通过配置改用兼容服务提供的推理模型。

## 3. 启动 Spring Boot

Spring Boot 需要指向 Python 服务，并配置相同的内部令牌：

```powershell
$env:AI_ENGINE = "langgraph"
$env:AI_SERVICE_URL = "http://localhost:8000"
$env:AI_SERVICE_INTERNAL_BEARER_TOKEN = "与Python一致的服务令牌"
```

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

## 4. 健康检查

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

也兼容 `/internal/v1/health/live` 和 `/internal/v1/health/ready`。ready 检查会反映 Redis checkpoint 状态；Redis 未启动且 `AI_SERVICE_REDIS_REQUIRED=true` 时服务应视为不可用。

## 5. Docker 启动

确保 Docker daemon 已启动，在 `ai-service` 目录执行：

```powershell
docker build -t yu-ai-service:local .
docker run --rm --name yu-ai-service -p 8000:8000 --env-file .env yu-ai-service:local
```

容器内访问宿主机 Spring 和 Redis 时，`.env` 中的地址使用：

```dotenv
AI_SERVICE_SPRING_GATEWAY_BASE_URL=http://host.docker.internal:8123/api/internal/ai-tools
AI_SERVICE_REDIS_URL=redis://host.docker.internal:6379/2
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

前端仍消费原有 SSE：`data: {"d":"..."}`，Python 与 Spring 之间使用 `application/x-ndjson`。

## 7. 测试与排查

```powershell
cd ai-service
uv run pytest
uv lock --check
```

常见问题：

- `401 Invalid internal bearer token`：检查 Python 的 `AI_SERVICE_INTERNAL_BEARER_TOKEN` 与 Spring `AI_SERVICE_INTERNAL_BEARER_TOKEN` 是否完全一致。
- ready 返回 503：检查 Redis 地址、端口、database 和 `AI_SERVICE_REDIS_REQUIRED` 配置。
- 工具调用失败：确认 Python 的 Spring 网关地址包含 `/api/internal/ai-tools`，且工具令牌与 Spring `ai.token` 一致。
- 模型调用失败：检查 API Key、Base URL、模型名称和网络连通性；不要把密钥写入 Git。
- Docker 无法构建：先启动 Docker daemon，再执行 `docker build`。

## 8. 安全要求

`.env` 仅用于本机或部署环境，不得提交。生产环境必须使用密钥管理系统或运行时环境变量，并轮换历史中曾暴露的 AI、OSS、邮件凭据。Python 服务不应直接挂载项目目录，所有文件和构建操作必须经过 Spring 工具网关。
