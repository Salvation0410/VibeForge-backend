# yu-ai-code-mother

一个通过自然语言生成网页应用的平台。描述需求后，可以查看生成过程、预览页面、继续修改，并部署应用或下载源码。

本仓库包含 **Spring Boot 业务后端**和 **Python AI 服务**；Vue 前端位于独立仓库 `yu-ai-code-mother-frontend`。所有模型调用由 Python 服务执行。

## 主要功能

- **应用生成**：支持单页 HTML、多文件网页和 Vue 工程，提供流式输出、质量检查与有限自动修复。
- **对话修改**：围绕已有应用继续提出需求，查看聊天记录、停止生成、导出对话。
- **预览与交付**：预览生成结果、部署应用、下载项目源码。
- **用户与社区**：注册登录、应用管理、作品分享、评论点赞和管理后台。
- **知识库客服**：可选的知识文档管理与 RAG 问答，需单独配置相关服务。

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

样例默认开启 PostgreSQL checkpoint。首次本地体验可将 `AI_SERVICE_CHECKPOINT_ENABLED=false`；需要持久化工作流状态时，按 [Python AI 服务说明](ai-service/README.md) 配置独立 PostgreSQL 数据库。本机运行时，样例中的 `host.docker.internal` 地址需改为对应的本机地址。

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
