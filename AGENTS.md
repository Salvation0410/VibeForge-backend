# AGENT.md

本文件是给后续 AI Agent / 开发协作者使用的项目工作指南。修改代码前先读本文件，再结合当前任务查看相关源码与文档。

## 项目概览

`yu-ai-code-mother` 是一个基于 Spring Boot 3.5.4 + Java 21 的后端项目，核心能力是 AI 代码生成、应用管理、聊天历史、用户体系、社区内容与管理后台。

主要技术栈：

- Java 21
- Spring Boot Web / AOP / Mail / Cache
- MyBatis-Flex + MySQL
- Redis / Spring Session / Redisson / Caffeine
- LangChain4j、LangGraph4j、DashScope
- Selenium + WebDriverManager
- Knife4j / springdoc-openapi
- Lombok

默认本地服务：

- 后端端口：`8123`
- Context path：`/api`
- Swagger / Knife4j 接口文档通常在后端启动后通过 Knife4j 入口访问
- 默认 MySQL：`jdbc:mysql://localhost:3306/yu_ai_code_mother`
- 默认 Redis：`localhost:6379`，database `1`

## 常用命令

在仓库根目录执行：

```powershell
# 编译
.\mvnw.cmd compile

# 运行全部测试
.\mvnw.cmd test

# 跳过测试打包
.\mvnw.cmd package -DskipTests

# 启动后端
.\mvnw.cmd spring-boot:run
```

本仓库还提供了接口脚本：

```powershell
.\scripts\test-user-controller.ps1
.\scripts\test-app-controller.ps1
.\scripts\test-community-controller.ps1
.\scripts\seed-community-posts.ps1
```

运行接口脚本前确认后端已启动，并且本地 MySQL、Redis 配置与 `src/main/resources/application.yml` 一致。

## 目录结构

- `src/main/java/com/yupi/yuaicodemother/controller`：HTTP 控制器层，统一返回 `BaseResponse<T>`。
- `src/main/java/com/yupi/yuaicodemother/service`：业务接口。
- `src/main/java/com/yupi/yuaicodemother/service/impl`：业务实现，常见实现继承 MyBatis-Flex `ServiceImpl`。
- `src/main/java/com/yupi/yuaicodemother/mapper`：MyBatis-Flex Mapper 接口。
- `src/main/resources/mapper`：XML SQL 映射文件。
- `src/main/java/com/yupi/yuaicodemother/model/entity`：数据库实体。
- `src/main/java/com/yupi/yuaicodemother/model/dto`：请求 DTO。
- `src/main/java/com/yupi/yuaicodemother/model/vo`：响应 VO。
- `src/main/java/com/yupi/yuaicodemother/common`：通用响应、分页和请求对象。
- `src/main/java/com/yupi/yuaicodemother/exception`：业务异常和全局异常处理。
- `src/main/java/com/yupi/yuaicodemother/aop`、`annotation`：权限校验等切面能力。
- `src/main/java/com/yupi/yuaicodemother/ratelimit`：限流注解、切面和 Redisson 配置。
- `src/main/java/com/yupi/yuaicodemother/ai`：LangChain4j AI 服务、路由服务、工具调用模型。
- `src/main/java/com/yupi/yuaicodemother/langraph4j`：LangGraph4j 工作流、节点、工具和状态对象。
- `src/main/java/com/yupi/yuaicodemother/core`：代码生成后的解析、保存、构建和流式处理。
- `src/main/resources/prompt`：AI 系统提示词。
- `sql`：初始化表结构与增量变更 SQL。
- `doc`：需求、接口说明、前端对接文档和测试说明。
- `scripts`：本地接口测试和数据初始化脚本。

## 核心业务模块

用户模块：

- 控制器：`SysUserController`
- 服务：`SysUserService` / `SysUserServiceImpl`
- 负责注册、邮箱验证码、图形验证码、登录、退出、个人资料、管理员用户管理。

应用与代码生成模块：

- 控制器：`AppController`
- 服务：`AppService` / `AppServiceImpl`
- AI 入口：`AiCodeGeneratorService`、`AiCodeGeneratorServiceFactory`、`AiCodeGenTypeRoutingService`
- 工作流：`CodeGenWorkflow`、`CodeGenConcurrentWorkflow`
- 代码保存/解析：`core/saver`、`core/paser`
- 构建：`VueProjectBuilder`
- 支持 SSE 流式生成、部署、下载、应用广场和后台管理。

聊天历史模块：

- 控制器：`ChatHistoryController`
- 服务：`ChatHistoryService`、`ChatHistoryOriginalService`、`ChatHistoryExportService`
- 负责按应用查询历史、后台分页、Markdown 导出。

社区模块：

- 控制器：`CommunityPostController`、`CommunityCommentController`、`CommunityTagController`
- 服务：`CommunityPostService`、`CommunityCommentService`、`CommunityTagService` 等
- 负责帖子、评论、标签、点赞、审核、置顶、个人主页内容。

管理后台：

- 控制器：`AdminDashboardController`
- 返回管理端概览统计数据。

静态资源：

- 控制器：`StaticResourceController`
- 用于访问已部署应用的静态资源。

## 编码约定

- 新增接口优先遵循现有 Controller -> Service -> Mapper 分层。
- Controller 返回 `ResultUtils.success(data)` 或抛出 `BusinessException`，不要直接返回裸对象。
- 参数校验失败使用 `ThrowUtils.throwIf(...)` 或抛出 `BusinessException(ErrorCode.PARAMS_ERROR, "...")`。
- 权限控制使用 `@AuthCheck`；管理员接口使用 `@AuthCheck(mustRole = UserConstant.ADMIN_ROLE)`。
- 需要限流的接口使用 `@RateLimit`，参考 `AppController` 的 SSE 代码生成接口。
- 数据库访问优先使用 MyBatis-Flex 的 QueryWrapper / Service 方法；复杂 SQL 放到 `src/main/resources/mapper`。
- 新增实体字段时，同步更新 entity、DTO/VO、mapper XML、SQL 迁移文件和相关测试。
- 新增 AI 提示词时放入 `src/main/resources/prompt`，命名保持业务含义清晰。
- 代码生成相关类型区分 HTML、多文件、Vue 项目等，修改时确认 `CodeGenTypeEnum`、Parser、Saver、Builder、Workflow 是否需要一起调整。
- 不要把生成文件、运行日志、临时下载产物提交进版本库。
- 当前部分源码注释存在历史编码乱码。除非任务明确要求修复编码，否则不要大面积重写无关注释，避免产生巨大无关 diff。

## API 与响应规范

统一响应结构：

```json
{
  "code": 0,
  "data": {},
  "message": "ok"
}
```

常用错误码定义在 `ErrorCode`：

- `40000`：请求参数错误
- `40100`：未登录
- `40101`：无权限
- `40300`：禁止访问
- `40400`：数据不存在
- `42900`：请求过于频繁
- `50000`：系统内部异常
- `50001`：操作失败

接口路径需要叠加全局前缀 `/api`。例如 `AppController` 的 `/apps/add` 实际访问路径是 `/api/apps/add`。

## 配置与敏感信息

`src/main/resources/application.yml` 当前包含本地开发默认配置。后续 Agent 修改时注意：

- 不要提交真实生产密码、AK/SK、API Key、邮箱授权码等敏感信息。
- 如需新增密钥配置，优先使用 profile、环境变量或外部配置。
- 本地 profile 当前为 `local`，提交前确认没有把个人机器路径写死到通用配置。
- OSS、邮件、AI 模型、DashScope、OpenAI 兼容接口等配置应放在配置类或 profile 中集中管理。

## 数据库

初始化 SQL 位于：

- `sql/init_database.sql`

增量 SQL 位于：

- `sql/alter_*.sql`

做数据库相关修改时：

- 先确认 entity 与表字段是否一致。
- 修改 Mapper XML 时同步检查对应 Mapper 接口和 Service 调用。
- 新增字段要考虑是否需要默认值、索引、逻辑删除、审核状态、排序字段和时间字段。
- 社区、用户、应用模块通常涉及 VO 聚合查询，字段变更后要检查分页列表与详情接口。

## 测试策略

优先运行与改动相关的 JUnit 测试：

```powershell
.\mvnw.cmd test -Dtest=ClassNameTest
```

常见测试目录：

- `src/test/java/com/yupi/yuaicodemother/ai`
- `src/test/java/com/yupi/yuaicodemother/core`
- `src/test/java/com/yupi/yuaicodemother/langraph4j`
- `src/test/java/com/yupi/yuaicodemother/service`
- `src/test/java/com/yupi/yuaicodemother/utils`

改动 Controller 行为后，可以在后端启动后运行对应 PowerShell 脚本做接口回归。

注意：AI、截图、外部搜索、OSS、邮件、浏览器驱动相关测试可能依赖本地环境、网络、密钥或 Chrome。若无法运行，必须在交付说明中明确说明原因。

## AI 代码生成工作流注意事项

代码生成链路涉及多个模块，修改时不要只看一个类：

1. `AppController` 接收请求并处理 SSE。
2. `AppService` 处理应用权限、状态、部署等业务。
3. `AiCodeGenTypeRoutingService` 判断生成类型。
4. `AiCodeGeneratorService` 或 LangGraph4j Workflow 执行生成。
5. `core/paser` 解析 AI 输出。
6. `core/saver` 保存生成文件。
7. `VueProjectBuilder` 构建 Vue 项目。
8. `ChatHistoryService` 记录对话。
9. `ProjectDownloadService` 处理下载。

涉及流式输出时，检查：

- `ai/model/message`
- `core/handler`
- `dev/langchain4j` 下本项目覆盖或适配的 LangChain4j 类
- 前端消费 SSE 的事件格式文档

## 交付前检查

提交或交付前至少做这些检查：

- `git status --short`，确认只包含本任务相关文件。
- 对 Java 改动运行相关测试，能跑全量时运行 `.\mvnw.cmd test`。
- 对配置改动检查是否包含敏感信息。
- 对 SQL 改动检查是否有向后兼容风险。
- 对接口改动同步更新 `doc/frontend` 或相关接口说明。
- 对代码生成链路改动至少跑一个 Parser/Saver/Workflow 相关测试。

## 给 Agent 的工作建议

- 先用 `rg` 搜索调用链，再改代码。
- 小范围修改优先，不做无关重构。
- 不要删除用户已有日志、临时文件或未跟踪文件，除非任务明确要求。
- 遇到本地环境依赖失败时，记录失败命令和直接原因，不要假装测试通过。
- 如果修改跨 Controller、Service、Mapper、SQL、文档多个层次，交付说明要列清楚影响面。
- 保持中文业务命名与现有风格一致；Java 包名、类名、字段名遵循现有英文命名。
