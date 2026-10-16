# yu-ai-code-mother 后端

AI 应用生成平台的后端仓库，包含 Spring Boot 业务服务和 `ai-service/` 下的 Python AI 服务。Vue 前端使用独立仓库，本仓库不包含前端页面。

本文以当前源码和配置为准，面向本地启动、服务联调和 Git 仓库交付。

## 已实现功能

- 用户注册、登录、验证码、资料管理与权限控制。
- 应用管理、HTML / MULTI_FILE / VUE_PROJECT 三类代码生成、流式输出、停止生成与聊天记录导出。
- 项目文件读写、解析、构建、预览、部署和源码下载。
- 社区帖子、评论、点赞、标签与管理后台。
- 客服问答和知识文档管理；启用 RAG 需要单独配置模型、OSS、Milvus 和重排器。
- AI 引擎切换：Legacy LangChain4j、Python LangGraph，以及按白名单或比例灰度。

HTML 和多文件产物经过校验及不可变版本发布后才成为活动版本；HTML 默认还需通过浏览器烟测。失败或取消不应覆盖上一活动版本。

## 架构与技术栈

```text
Vue 前端（独立仓库）
  -> Spring Boot：权限 / 业务数据 / 对外 SSE / 项目文件 / 构建与发布
     -> AiGenerationGateway
        -> Legacy LangChain4j
        或 Python FastAPI -> LangGraph -> 模型 / Spring 内部工具网关
```

| 组件 | 当前技术与职责 |
| --- | --- |
| Spring 业务后端 | Java 21、Spring Boot 3.5.4、MyBatis-Flex 1.11.0、MySQL |
| 会话与缓存 | Redis、Spring Session、Redisson、Caffeine |
| Python AI 服务 | Python 3.12、FastAPI、LangChain、LangGraph、uv |
| 模型接入 | DeepSeek / OpenAI 兼容接口，Java 保留 LangChain4j |
| 文件与浏览器 | 阿里云 OSS、Selenium、WebDriverManager、Chrome |
| 接口文档 | Knife4j、springdoc-openapi |
| 可选基础设施 | PostgreSQL checkpoint、Milvus、Embedding、Reranker、LangSmith |

Spring 是业务数据库和项目文件的唯一所有者。Python 不连接业务 MySQL，不直接操作生成项目目录，文件与构建操作通过 Spring 内部工具网关完成。服务间流使用 NDJSON，对前端仍提供 SSE。

`src/main/java/com/yupi/yuaicodemother/langraph4j/` 是历史 Java 图实验代码，不是当前 Python LangGraph 主链路。PostgreSQL checkpoint 用于短期工作流状态，不是跨请求长期记忆库。

## 仓库目录

```text
src/main/java/com/yupi/yuaicodemother/
  controller/        HTTP 接口
  service/           业务逻辑
  mapper/            MyBatis-Flex 数据访问
  model/             Entity、DTO、VO
  ai/gateway/        AI 引擎统一边界与路由
  core/              解析、保存、构建、产物发布、流处理
  config/            Spring 配置
src/main/resources/
  application.yml                  公共配置，当前默认 local + langgraph
  application-local.example.yml    可提交的本地配置样例
  application-local.yml            私有本地配置，不提交
  mapper/                          Mapper XML
  prompt/                          Legacy 提示词
src/test/            Java 测试
ai-service/          Python AI 服务、测试、Dockerfile、uv.lock 与 .env.example
sql/                 数据库初始化与增量 SQL
scripts/             本地验证及回归脚本
doc/                 设计、启动与交接文档
docs/superpowers/    设计与实施过程文档
tmp/                 生成源码、发布产物等运行数据，不提交
```

## 环境要求

| 依赖 | 使用要求 | 默认连接 |
| --- | --- | --- |
| JDK | 21 | 配置 `JAVA_HOME` |
| Maven | 可使用仓库 Maven Wrapper，固定 3.9.11 | 首次使用需下载依赖 |
| MySQL | 建议 8.x，并创建业务表 | `localhost:3306/yu_ai_code_mother` |
| Redis | 会话、限流、生成状态及内部工具幂等依赖 | `localhost:6379`，Spring database 1 |
| Python + uv | Python 严格使用 3.12，默认 LangGraph 引擎需要 | `localhost:8000` |
| Node.js + npm | Vue 生成项目的依赖安装和构建需要，须在 Spring 进程 PATH 中可用 | 由生成项目决定具体版本要求 |
| Chrome / WebDriver | HTML 默认浏览器烟测、截图功能需要 | 无头运行 |
| PostgreSQL | 开启 checkpoint 时使用，建议 15+ | `localhost:5432/yu_ai_checkpoint` |

邮件注册验证码需要 SMTP；头像、封面、社区图片和知识文档上传需要 OSS。占位配置只描述配置结构，不能完成真实外部调用。

## 本地启动

以下命令使用 PowerShell，Spring 命令在仓库根目录执行。

### 1. 准备数据库与 Redis

先启动 MySQL 和 Redis。在 MySQL 客户端执行：

```sql
SOURCE sql/init_database.sql;
SOURCE sql/alter_customer_service_knowledge.sql;
SOURCE sql/alter_customer_service_knowledge_task7.sql;
```

`SOURCE` 路径相对于 MySQL 客户端工作目录，也可在数据库工具中依次打开执行。以上面向新建数据库；现有数据库应先备份，再核对表结构和 SQL 内容后选择增量脚本。初始化脚本不是所有语句都可重复执行，不能直接对已有数据库反复运行。不要使用带有真实业务数据的个人数据库导出代替初始化脚本。

`sql/alter_customer_service_knowledge_pending_time.sql` 是旧版本首次待处理任务的时间修复脚本，新建空库不需要执行；已有库按脚本中的查询结果判断是否需要修复。

### 2. 准备 Spring 私有配置

```powershell
# 仅首次创建时复制，保留已有本地配置。
if (-not (Test-Path src/main/resources/application-local.yml)) {
  Copy-Item src/main/resources/application-local.example.yml src/main/resources/application-local.yml
}
```

编辑 `application-local.yml`，填写数据库密码、模型密钥和需要启用的外部服务配置。也可通过样例中对应的环境变量注入。当前公共配置默认激活 `local`，样例文件本身不会被 Spring 自动加载。

| 配置 | 样例中的环境变量 | 说明 |
| --- | --- | --- |
| MySQL | `MYSQL_URL`、`MYSQL_USERNAME`、`MYSQL_PASSWORD` | 必须连接已初始化的数据库 |
| Redis | `REDIS_HOST`、`REDIS_PORT`、`REDIS_PASSWORD`、`REDIS_DATABASE` | 默认不设密码；有认证时填写 |
| Java 模型 | `MODEL_BASE_URL`、`MODEL_API_KEY`、`MODEL_NAME` | Java 模型 Bean 和辅助服务仍保留，不能因切到 Python 就删除整段配置 |
| Java 推理模型 | `REASONING_MODEL_BASE_URL`、`REASONING_MODEL_API_KEY`、`REASONING_MODEL_NAME` | Legacy Vue 推理模型；密钥需单独填写 |
| AI 引擎 | `AI_ENGINE`、`AI_SERVICE_URL` | 当前默认 `langgraph`；也支持 `legacy`、`gray`、`auto` |
| 内部鉴权 | `AI_SERVICE_INTERNAL_BEARER_TOKEN` | Spring / Python 共用的高强度随机令牌 |
| SMTP | `MAIL_HOST`、`MAIL_PORT`、`MAIL_USERNAME`、`MAIL_PASSWORD` | 样例按 SSL 465 配置，其他协议需同步调整 |
| OSS | `OSS_BUCKET_NAME`、`OSS_ACCESS_KEY_ID`、`OSS_ACCESS_KEY_SECRET`、`OSS_ENDPOINT`、`OSS_WEB_SITE` | 按实际地域、Bucket 和访问域名填写 |
| 客服开关 | `AI_CUSTOMER_SERVICE_ENABLED` | 样例默认关闭，按需联调 |

样例默认关闭模型请求、响应日志，减少聊天内容及源码进入日志的风险。公共配置中的数据库密码是开发默认值，应在私有配置覆盖。Spring 不会自动读取 `ai-service/.env`；上述自定义环境变量通过复制后的 YAML 占位符绑定。

### 3. 准备 Python AI 服务

默认 `langgraph` 引擎需要 Python 服务。新开一个终端：

```powershell
Set-Location ai-service
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 编辑 .env 后再同步依赖并启动。
uv sync --frozen --python 3.12
uv run python -m ai_service.server
```

Windows 使用 `ai_service.server`，该入口配置适合 psycopg 异步 checkpoint 的事件循环。详细初始化、依赖安装故障、Docker 和 PostgreSQL 配置见 [AI 服务说明](ai-service/README.md)。

至少核对以下配置：

- 将 `.env` 的 `AI_SERVICE_MODEL_API_KEY` 替换为真实密钥，核对 `AI_SERVICE_MODEL_BASE_URL` 和 `AI_SERVICE_MODEL_NAME`。
- 本机直接运行 Python 时，将 `AI_SERVICE_SPRING_GATEWAY_BASE_URL` 改为 `http://localhost:8123/api/internal/ai-tools`；现有 `.env.example` 的 `host.docker.internal` 适用于容器访问宿主机的场景。
- 开启 checkpoint 时，配置独立 PostgreSQL 数据库和 `AI_SERVICE_CHECKPOINT_POSTGRES_URL`；首次仅联调且暂不使用持久化时，可设 `AI_SERVICE_CHECKPOINT_ENABLED=false`。
- Spring `ai.token`、Python `AI_SERVICE_INTERNAL_BEARER_TOKEN`、Python `AI_SERVICE_SPRING_GATEWAY_BEARER_TOKEN` 必须一致。现有 Python 样例的两个占位符不同，复制后必须手动统一。

令牌使用自行生成的高强度随机值，不要把样例占位符用于实际部署。令牌是静态共享密钥，没有自动过期能力。

### 4. 启动 Spring

```powershell
.\mvnw.cmd spring-boot:run
```

默认监听 `http://localhost:8123/api`，接口文档为 `http://localhost:8123/api/doc.html`，OpenAPI 为 `http://localhost:8123/api/v3/api-docs`。

如果使用环境变量，需在启动 Spring 的同一终端设置；例如：

```powershell
$env:AI_ENGINE = "langgraph"
$env:AI_SERVICE_URL = "http://localhost:8000"
# 内部令牌从私有配置或安全渠道注入。
.\mvnw.cmd spring-boot:run
```

切换到 `legacy` 可不启动 Python 生成服务，但仍需要 Java 模型密钥、MySQL、Redis 及所用功能的依赖。

### 5. 检查服务

```powershell
Invoke-WebRequest http://localhost:8123/api/v3/api-docs
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Spring OpenAPI 可访问仅说明 HTTP 文档入口可用，不代表所有业务依赖正常。Python `live` 检查进程存活，`ready` 检查 checkpoint 状态，均不保证真实模型、工具网关或客服 RAG 已通过联调。

## 接口与文件说明

| 路径 | 用途 |
| --- | --- |
| `/api/users` | 用户相关接口 |
| `/api/apps` | 应用管理 |
| `GET /api/apps/chat/gen/code` | 代码生成 SSE |
| `POST /api/apps/chat/gen/code/cancel` | 停止生成 |
| `/api/chatHistory` | 聊天记录查询和导出 |
| `POST /api/apps/deploy` | 应用部署 |
| `GET /api/apps/download/{appId}` | 源码下载 |
| `/api/community/posts`、`/api/community/comments`、`/api/community/tags` | 社区功能 |
| `/api/admin/dashboard/overview` | 管理概览 |
| `/api/customer-service/ask` | 客服问答 |
| `/api/admin/customer-service/knowledge` | 管理员知识文档管理 |
| `/api/internal/ai-tools/invoke` | Python 调用的内部工具网关，须携带 Bearer 令牌 |

普通 Spring 接口统一返回 `code`、`data`、`message`；SSE、文件下载和静态资源按各自协议返回。Python 内部接口不使用 Spring `BaseResponse`。具体参数及权限以源码和 OpenAPI 文档为准。

生成目录位于启动工作目录下的 `tmp/code_output/`，部署目录位于 `tmp/code_deploy/`。请从固定工作目录启动服务，并为运行数据设置持久存储。部署地址还需与资源控制器、端口和反向代理保持一致，不能仅替换一个展示域名就认为部署完成。

客服首次启动建议保持关闭。启用时按 [AI 服务说明](ai-service/README.md) 配置 RAG；Spring 与 Python 的开关都需检查。样例上传限制为 5 MB，而知识文档业务上限默认 20 MiB，需要大文档时同时调整 `spring.servlet.multipart` 和反向代理的上传限制。

## 构建与验证

```powershell
# Java 干净编译、指定测试、打包
.\mvnw.cmd clean compile -DskipTests
.\mvnw.cmd test "-Dtest=InternalAiToolsControllerTest"
.\mvnw.cmd package -DskipTests

# 运行 JAR，继续使用 local 配置或外部注入配置
java -jar target/yu-ai-code-mother-0.0.1-SNAPSHOT.jar
```

`application-local.yml` 放在资源目录时会被打进本机 JAR；不要公开包含私有配置的构建产物。需要分发时，将私有配置移至运行目录的 `config/application-local.yml` 等外部配置位置后重新干净构建。

```powershell
Set-Location ai-service
uv run python -m compileall -q src
uv run pytest
uv lock --check
```

Java 全量测试可运行 `.\mvnw.cmd test`，其中部分测试依赖真实模型、邮件、OSS、Chrome 或本地基础设施。Python 常规测试使用 Fake / 注入式 Provider；真实 PostgreSQL、模型与跨服务端到端效果需另外验证。构建跳过测试不等于测试通过。

## 上传 Git 前

提交 `README.md`、`src/main/resources/application.yml`、`src/main/resources/application-local.example.yml`、`ai-service/.env.example` 等公共文件；本地真实配置留在忽略文件中。

```powershell
git status --short
git diff --check
git check-ignore src/main/resources/application-local.yml ai-service/.env
git ls-files src/main/resources/application-local.yml ai-service/.env
```

最后一条正常应无输出。`.gitignore` 不会移除已经追踪的文件，历史提交中的秘密也不会因当前忽略规则而消失；如曾泄露，应轮换凭据并单独处理 Git 历史。不要上传 `.env`、本地私有配置、日志、`target/`、`.venv/`、`tmp/`、`projects/` 或数据库业务数据导出。

仓库可能存在与本次文档无关的修改或删除，上传前逐项检查，避免直接使用 `git add .` 混入无关内容。Git 提交与远程推送由维护者手动执行。

## 相关文档

- [Python AI 服务完整说明](ai-service/README.md)
- [AI 服务启动与联调](doc/ai-service-startup.md)
- [LangChain / LangGraph 重构设计](doc/ai-service-langchain-langgraph-refactor-design.md)
- [第一阶段交接](doc/ai-service-phase-one-handoff.md)
- [产品知识库说明](doc/knowledge/product/README.md)
- [协作规范](AGENTS.md)
