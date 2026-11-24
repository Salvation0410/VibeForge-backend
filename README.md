# yu-ai-code-mother

一个通过自然语言生成网页应用的平台。描述需求后，可以查看生成过程、预览页面、继续修改，并部署应用或下载源码。

本仓库包含 **Spring Boot 业务后端**和 **Python AI 服务**；Vue 前端位于独立仓库 `yu-ai-code-mother-frontend`。所有模型调用由 Python 服务执行。

## 主要功能

- **应用生成**：支持单页 HTML、多文件网页和 Vue 工程，提供流式输出、质量检查与有限自动修复。

- **对话修改**：围绕已有应用继续提出需求，查看聊天记录、停止生成、导出对话。
- **预览与交付**：预览生成结果、部署应用、下载项目源码。
- **用户与社区**：注册登录、应用管理、作品分享、评论点赞和管理后台。
- **知识库客服**：可选的知识文档管理与 RAG 问答，需单独配置相关服务。

Vue生成与修复默认共享40次模型工具、最多两次正式修复。逐项修复的计划拒绝提供固定原因及安全计数诊断；未写入就切换工作集时会提示沿活动计划继续。构建与审查通过仍需结合实际浏览器操作验收，Vue失败暂不回滚源码或dist。实现边界见 [Python AI 服务说明](ai-service/README.md)，本轮代码、真实模型和浏览器结果分别记录在 [质量优化交接第十三节](doc/generation-quality-optimization-20261123-handoff.md#十三2026-11-24-计划拒绝安全分类与新真实样本)。

## 技术架构

| 服务 | 技术栈 | 职责 |
| --- | --- | --- |
| 业务后端 | Java 21、Spring Boot 3.5.4、MyBatis-Flex、MySQL、Redis | 用户权限、业务数据、项目文件、构建与部署 |
| AI 服务 | Python 3.12、FastAPI、LangChain、LangGraph | 模型调用、生成编排、审查与修复 |
| 前端（独立仓库） | Vue 3、TypeScript、Vite | 生成对话、应用预览与管理界面 |

前端通过 Spring 接口发起请求并接收 SSE 输出。Spring 调用 Python AI 服务，Python 通过内部工具网关使用 Spring 的文件和构建能力。业务数据库与生成项目文件统一由 Spring 管理。

## 本地启动

以下命令使用 PowerShell，默认从本仓库根目录执行。

### 1. 准备环境和数据库

安装 JDK 21、Maven、Python 3.12、uv、MySQL 和 Redis，并将命令行工具加入 PATH。

前端运行及 Vue 工程生成需要 Node.js 和 npm，且 Spring 进程应能访问这两个命令。HTML 默认浏览器检查及部署截图需要 Chrome 和匹配的 WebDriver。

启动 MySQL、Redis 后，在 MySQL 客户端依次执行以下脚本（路径相对于仓库根目录）：

```sql
SOURCE sql/init_database.sql;
SOURCE sql/alter_customer_service_knowledge.sql;
SOURCE sql/alter_customer_service_knowledge_task7.sql;
```

上述步骤用于新建数据库。已有数据库请按 [数据库升级说明](doc/generation-task-ledger.md) 核对增量迁移，勿重复执行初始化脚本；Spring 不会自动迁移数据库。

### 2. 配置服务

首次使用时复制配置样例：

```powershell
if (-not (Test-Path src/main/resources/application-local.yml)) {
  Copy-Item src/main/resources/application-local.example.yml src/main/resources/application-local.yml
}
if (-not (Test-Path ai-service/.env)) {
  Copy-Item ai-service/.env.example ai-service/.env
}
```

编辑 `src/main/resources/application-local.yml`：

- 填写 MySQL、Redis 连接信息。
- 设置 `ai.service-url`，本机默认为 `http://localhost:8000`。
- 将 `ai.token` 设置为自行生成的高强度随机共享令牌。

编辑 `ai-service/.env`：

| 配置项 | 填写内容 |
| --- | --- |
| `AI_SERVICE_MODEL_API_KEY` | 模型供应商 API Key |
| `AI_SERVICE_MODEL_BASE_URL` | 模型接口地址，样例为 `https://api.deepseek.com/v1` |
| `AI_SERVICE_MODEL_NAME` | 模型名称，样例为 `deepseek-chat` |
| `AI_SERVICE_INTERNAL_BEARER_TOKEN` | 与 Spring 的 `ai.token` 一致 |
| `AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN` | 与 Spring 的 `ai.token` 一致 |
| `AI_SERVICE_SPRING_GATEWAY_BASE_URL` | 本机运行填写 `http://localhost:8123/api/internal/ai-tools` |

样例默认开启 PostgreSQL checkpoint，并使用宿主机 `localhost` 地址；按 [Python AI 服务说明](ai-service/README.md) 配置独立 PostgreSQL 数据库。Docker 容器连接宿主机时需改用 `host.docker.internal` 或实际可达地址。从原项目迁移时优先复用原私有配置，不要以样例占位密钥覆盖有效配置；两端配置核对与本轮测试结果见 [配置修复与运行验证](docs/python-ai-only-runtime-validation.md)。

模型密钥只需配置在 Python 服务。Spring 不会读取 `ai-service/.env`，两端的共享令牌需要分别填写。

按需补充外部服务：邮件注册验证码需要 SMTP，头像、封面及文档上传需要 OSS，图片搜索需要 Pexels API Key。客服功能默认关闭，启用方式见 [Python AI 服务说明](ai-service/README.md)。

启动时需保留样例中的 `spring.mail` 配置，至少提供 `host` 和 `username`；发送验证码还需真实的 SMTP 凭据。若提示缺少 `JavaMailSender`，先检查 `application-local.yml` 是否存在且已加载，`.example.yml` 不会自动生效。

### 3. 启动后端与 AI 服务

在仓库根目录启动 Spring：

```powershell
mvn spring-boot:run
```

另开终端，从仓库根目录启动 Python AI 服务：

```powershell
Set-Location ai-service
uv sync --frozen --python 3.12
uv run python -m ai_service.server
```

Windows 请使用上述 Python 入口，它已适配异步 PostgreSQL 所需的事件循环。

### 4. 启动前端

准备独立前端仓库。若与本仓库位于同一级目录，在新的终端从本仓库根目录执行：

```powershell
Set-Location ../yu-ai-code-mother-frontend
npm install
npm run dev
```

| 入口 | 默认地址 |
| --- | --- |
| 前端页面 | `http://localhost:5173` |
| Spring 接口 | `http://localhost:8123/api` |
| 接口文档 | `http://localhost:8123/api/doc.html` |
| Python 存活检查 | `http://localhost:8000/health/live` |
| Python 就绪检查 | `http://localhost:8000/health/ready` |

Python 健康检查不调用真实模型；服务启动后，还需通过前端实际生成应用来验证模型和跨服务配置。

## LangSmith 监控与图调试

本版本已接入 Python LangSmith 脱敏追踪，也提供 LangGraph Studio 图入口，无需增加 Java AI 框架。两者用途不同：LangSmith 查看正常业务请求的节点耗时和结果摘要；Studio 在独立本地 Agent Server 中查看图、设置断点及检查状态。

### 启动业务监控

复用 `ai-service/.env` 中原有的 LangSmith 配置，确认以下四项；已有真实密钥不要替换成占位值：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=填写已有的LangSmith密钥
LANGSMITH_PROJECT=Yuki
```

`Yuki` 是本次复用的本地项目名；其他环境以自己的配置为准。endpoint 必须与密钥所属区域一致。随后重启 Python，Spring 和前端按前文启动：

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-pythonAIOnly-version/ai-service
uv sync --frozen --python 3.12
uv run python -m ai_service.server
```

在前端生成一次应用，再进入 [LangSmith](https://smith.langchain.com) 的 Tracing，选择 `LANGSMITH_PROJECT` 对应项目，按元数据 `request_id` / `app_id` 找到相关节点和 `generation` 记录。当前记录是独立的脱敏摘要，输入/输出为空，不包含提示词、源码或完整模型消息，也不提供完整父子调用树和模型费用统计。

### 启动 Studio 图调试

保留正常业务服务，另开终端执行：

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-pythonAIOnly-version/ai-service
uv sync --frozen --python 3.12
uv run --no-sync --with "langgraph-cli[inmem]==0.4.43" --with "langgraph-api==0.5.42" python -m ai_service.studio_server
```

CLI 使用独立临时依赖环境，不修改业务依赖锁；这组版本已在当前锁定的 LangGraph 0.6.11 上验证。首次执行需下载依赖。等日志显示图 `yu_ai_generation` 注册完成，打开 [Studio](https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024)，选择该图；本地接口文档在 `http://127.0.0.1:2024/docs`。终端按 Ctrl+C 停止调试服务。

Studio 使用 2024 端口，正常 Python 服务仍是 8000；不要把 Spring 的 `ai.service-url` 改成 2024。Studio 不会自动显示前端生成任务，也不会读取业务 PostgreSQL 中的已有任务。它的启动入口在当前进程关闭云端自动追踪，不修改 `.env` 或另一个业务进程的追踪配置。

图输入字段、断点操作和发布限制见 [Python AI 服务：监控与图调试](ai-service/README.md#langsmith-监控与-studio-图调试)。首次调试建议在生成准备前暂停，只核对输入审核与上下文读取；不要直接对已有业务应用运行完整写文件/发布流程。

## 使用说明

登录后创建应用，输入页面需求，等待生成完成即可预览。继续发送需求可修改应用，完成后可部署或下载源码。

- HTML 和多文件网页通过校验后切换活动版本；Vue 工程直接修改文件，失败或停止时不保证回滚。
- 当前不支持生成任务自动恢复或 SSE 断线续传。
- 生成源码与部署文件分别保存在 `tmp/code_output/` 和 `tmp/code_deploy/`，路径相对于 Spring 启动目录；请保留运行数据。
- `.env`、本地配置及密钥不要提交。资源目录中的 `application-local.yml` 会进入本机 JAR，分发构建产物时应改用外部私有配置。

## 目录与文档

```text
src/          Spring 业务后端与测试
ai-service/   Python AI 服务、配置样例与测试
sql/          数据库初始化及迁移脚本
scripts/      本地验证脚本
doc/          专题使用与设计文档
docs/         迁移说明与实施记录
```

- [Python AI 服务配置与运行](ai-service/README.md)
- [生成任务与数据库升级](doc/generation-task-ledger.md)
- [客服知识库迁移](doc/customer-service-knowledge-publication-migration.md)
- [开发协作规范](AGENTS.md)

历史设计与验收记录保留在文档目录中；其中旧 Java AI 执行链路不适用于当前版本。
