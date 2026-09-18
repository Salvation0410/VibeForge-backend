# AI 服务中文 README 设计

## 目标

将 `ai-service/README.md` 改为可独立使用的完整中文开发手册。新开发者只阅读 README 即可理解服务边界、完成本地启动、检查服务状态并运行测试，同时可通过链接进入更详细的启动和架构文档。

## 内容结构

README 按以下顺序组织：

1. 项目简介：说明 FastAPI、LangChain、LangGraph 和 Python 3.12 技术栈。
2. 架构与职责边界：明确 Python 负责模型和编排，Spring 负责鉴权、数据库、文件、构建和对外 SSE。
3. 源码目录：展示 `api`、`orchestration`、`models`、`infrastructure` 四类职责包。
4. LangGraph 工作流：用文本流程说明输入校验、分支生成、工具调用、验证、构建、质检、最多两次修复和完成事件。
5. 环境要求：列出 Python 3.12、uv、Redis、模型 API 和 Spring 后端。
6. 配置说明：列出 `.env.example` 中所有配置的作用和默认行为，不包含真实密钥。
7. 本地启动：提供 Windows PowerShell 命令。
8. Docker 启动：提供构建、运行及宿主机地址说明。
9. 接口与事件协议：列出健康检查、路由、生成、取消接口及 NDJSON 事件类型。
10. 测试：提供 pytest、compileall 和锁文件检查命令。
11. Redis 与故障降级：解释 required 和 optional 两种模式。
12. 安全要求：说明 Bearer 令牌、密钥管理、文件访问边界和日志脱敏。
13. 相关文档：链接到中文启动说明和重构方案。

## 表达规则

- 正文使用中文，环境变量、HTTP 路径、类名、命令和协议名称保留原文。
- 以实际源码、`.env.example` 和接口定义为准，不描述尚未实现的能力。
- README 提供完整的快速使用路径；复杂联调和排障细节链接到 `doc/ai-service-startup.md`，避免两处维护大段重复内容。
- 不写入真实 API Key、服务令牌、个人路径或生产地址。

## 验证

- 对照 `ai-service/src/ai_service` 检查目录树和模块职责。
- 对照 `.env.example` 检查配置项完整性。
- 对照 `api/routes.py` 检查接口路径和鉴权描述。
- 运行 README 中的测试命令，确认命令有效。
- 运行 `git diff --check`，确认 Markdown 无格式错误。

## 非目标

- 不修改 Python 或 Spring 业务代码。
- 不新增部署编排、CI/CD 或生产基础设施配置。
- 不复制详细启动文档中的全部排障内容。
