# P0 验收加固与 LangSmith 旁路追踪设计

## 目标

完成第一阶段交接文档中尚未关闭的 P0 真实环境验收，修复一个模型路由错误信息泄露点，并为 Python LangGraph 工作流增加默认关闭、可脱敏、旁路式的 LangSmith 追踪能力。追踪故障不得改变生成、取消、失败、发布或旧版本保留语义。

## 范围

### P0 加固与验收

- 未知代码生成类型返回稳定的脱敏错误码，不把模型原始返回值放入 HTTP 响应。
- 验证独立 PostgreSQL checkpoint 的初始化、连接池、健康检查、终态清理、TTL 清理以及 optional/required/disabled 三种配置模式。
- 验证 Spring、Python、Vue 真实链路中的三种生成类型、二次修改、构建、质量修复、取消、断线、超时、工具失败、旧预览保留、历史回源和单次预览刷新。
- 验证 Redis 多 Spring 实例下的工具调用竞争、回放、冲突和不确定状态。
- 所有验收结果只写入 `target/ai-validation/` 的脱敏记录，不保存源码、完整提示词、工具参数、凭据、Cookie 或完整响应。

### LangSmith

- 默认关闭。只有显式设置追踪开关并提供运行时凭据时才启用。
- 追踪内容限制为 requestId、appId、userId（如允许）、engine、codeGenType、node、tool 名称、耗时、repair/tool 计数、终态和稳定错误码等脱敏元数据。
- 不上传完整 prompt、conversation、源码 artifact、tool arguments、tool results、模型原始响应、Bearer token、API key、Cookie 或绝对路径。
- LangSmith 初始化失败、发送失败和关闭失败只记录脱敏日志，不影响业务终态。
- 追踪配置和客户端生命周期由 Python AI 服务统一管理；不改变 Spring 对外 SSE 协议和内部 NDJSON 契约。
- 依赖和配置必须兼容当前 LangChain/LangGraph 锁定版本，默认测试不得访问 LangSmith 网络。

## 方案

在 `ai-service` 增加一个可选 tracing 适配层：应用启动时根据配置创建 LangSmith callback/tracer，工作流执行时传入稳定 tags 和 metadata；适配层在提交前执行白名单过滤，并将追踪异常隔离。关闭追踪时使用空实现，避免业务代码分支扩散。

配置至少包含追踪开关、项目名、端点、API key、采样比例和内容记录开关。内容记录默认关闭且代码层强制过滤，不能仅依赖环境变量表达“安全”。健康检查不因 LangSmith 不可用而失败；运行状态通过内部脱敏日志和验收记录观察。

## 验证

- Python：`compileall`、全量 `pytest`、`uv lock --check`，并增加追踪关闭、脱敏字段和追踪异常隔离测试。
- Java：路由错误脱敏相关测试、既有 LangGraph 定向测试和干净编译。
- 真实环境：PostgreSQL opt-in 集成、Redis 多实例竞争、三服务端到端和 LangSmith 显式开启后的脱敏验证。
- 提交前执行 `git diff --check`、双仓库 `git status --short --branch`，并检查提交信息符合中文 Conventional Commits。

## 回滚

优先将 LangSmith 开关设为关闭并重启 Python 实例；无需修改数据库、Spring、Vue 或 checkpoint schema。若 LangGraph 主链路异常，继续使用既有 `AI_ENGINE=legacy` 回滚。P0 验收记录保留失败证据，不通过修改记录伪造通过状态。

## 非目标

- 不在本阶段启用长期记忆 `PostgresStore`。
- 不在本阶段实施客服 RAG、Milvus、Embedding 或 GPU Reranker。
- 不删除 Legacy 引擎，不改变 Python 对 MySQL 和项目目录的访问边界。
