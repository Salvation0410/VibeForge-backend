# Python AI 专用版本迁移说明

迁移日期：2026-11-23。来源为 `D:/VibeForge/yu-ai-code-mother` 的提交 `3a05b9a2747e7e6f2bb847c40840e7eeca16486b`，目标为 `D:/VibeForge/yu-ai-code-mother-pythonAIOnly-version`。目标是独立 Git 仓库，保留来源历史，工作分支为 `codex/python-ai-only`，未配置远程推送地址。

## 当前架构

```text
独立 Vue 前端
  -> Spring Boot 业务后端
     -> AiGenerationGateway / LangGraphAiGenerationGateway（HTTP 客户端）
        -> Python FastAPI / LangChain / LangGraph
           -> 模型、审核、生成、修复、客服 RAG
           -> Spring 内部工具网关（文件、图片搜索、构建、发布）
```

`LangGraphAiGenerationGateway` 名称中的 LangGraph 指 Python 服务，Java 类本身只使用 HTTP/NDJSON，不运行图或供应商模型。

Spring 继续拥有业务 MySQL、权限、聊天历史、任务台账、发布仲裁、文件沙箱、构建、截图、部署与下载。Python 不直接访问业务 MySQL 或项目目录。现有三种生成类型、公共 SSE、内部工具契约、静态展示/候选分离、取消与有限修复边界保持原设计。

## 清理范围

- 删除 Java Legacy 生成、灰度委派、模型工厂、图实验、框架覆盖源码及专属提示词和测试。
- 删除 Maven 中的 Java AI 框架和旧实验图专用模型 SDK；流式处理显式依赖 Reactor。
- 图片搜索抽离旧图包，作为普通业务工具继续使用。流式消息和历史服务不依赖模型框架类型。
- Java 配置移除模型供应商参数与 engine/gray 选择项。Python 不可用时沿既有错误路径失败，没有 Java AI 回退。
- 删除双引擎对比脚本与 Java 回滚验收项，其余验收仍要求显式执行。

迁移时发现 Windows 自动换行转换会破坏知识库冻结哈希，现以 `.gitattributes` 固定相关文件的既有换行。冻结的 `KnowledgeDocumentFilePolicy.java` 原本混用 LF/CRLF，因此以原始字节保存并让空白检查识别行尾 CR；其业务代码未修改。已通过 Git clean/checkout 过滤器验证 22 个冻结文件哈希一致。

知识库中的旧索引、版本及兼容守卫仍属于 Python RAG 的数据兼容能力，不是 Java AI 执行链路，不作关键词式删除。`doc/`、`docs/superpowers/` 中旧架构设计与验收是历史资料；当前架构以根 README、AGENTS.md 和 ai-service/README.md 为准。

## 本机启动

从目标仓库启动，避免生成文件写到原仓库：

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-pythonAIOnly-version
if (-not (Test-Path src/main/resources/application-local.yml)) {
  Copy-Item src/main/resources/application-local.example.yml src/main/resources/application-local.yml
}
# 配置 MySQL、Redis、双向内部共享令牌及所需外部服务后启动。
.\mvnw.cmd spring-boot:run
```

另开终端：

```powershell
Set-Location D:/VibeForge/yu-ai-code-mother-pythonAIOnly-version/ai-service
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
# 在 .env 填写模型配置；本机工具地址使用 localhost:8123。
uv sync --frozen --python 3.12
uv run python -m ai_service.server
```

两个方向的 Bearer 令牌必须一致。默认服务端口与原项目一致，启动新版本前停止占用相同端口的旧实例，或显式调整端口及对应服务地址。使用已有数据库时不会自动迁移表；生产数据迁移、发布权威和回退限制继续遵循根 README 及生成任务说明。

首次迁移未复制 `.env`、`application-local.yml`、生成应用、日志或用户数据。后续按用户要求复用了原项目私有配置并完成真实生成验证，见 [配置修复与运行验证](python-ai-only-runtime-validation.md)。目标 `.venv` 在本机从锁文件独立安装，不提交 Git。Vue 前端仍使用同级独立仓库 `D:/VibeForge/yu-ai-code-mother-frontend`，不在迁移改动范围。

## 首次迁移验证范围

本轮实际执行结果：

| 检查 | 结果 |
| --- | --- |
| Maven compile / test-compile | 通过 |
| `mvn "-Dtest=*Test" test` | 546 项，492 通过、54 按真实资源条件跳过，0 失败/错误 |
| 工具开始进度恢复后的 4 类聚焦回归 | 48 项通过，0 失败/错误/跳过 |
| `mvn package -DskipTests` | 打包通过；这条命令本身跳过测试 |
| Maven 依赖树与 JAR 内容审计 | 无旧 Java AI 框架依赖、旧网关类或私有本地配置 |
| `uv sync --frozen --python 3.12` | 独立虚拟环境安装通过 |
| `uv run python -m compileall -q src`、`uv lock --check` | 通过 |
| `uv run pytest --tb=short` | 2617 通过、26 跳过，2 条依赖弃用警告 |
| PowerShell 验收脚本静态检查 | 通过 |
| 22 个冻结文件经 Git clean/checkout 过滤后校验 | 哈希全部匹配 |
| `git diff --check` | 通过 |

首轮 Python 回归暴露的换行和缺失离线环境配置问题已修复，并重新执行上述完整测试。未通过放宽冻结校验、修改期望哈希或复制真实密钥来绕过失败。

离线测试不能证明真实模型效果、完整业务启动或浏览器交互通过。本轮未运行依赖完整环境的 Spring context 测试，未执行真实收费模型生成、真实 MySQL/Redis/PostgreSQL/Milvus 集成、数据库迁移或服务切换。原项目与独立 Vue 前端未修改。
