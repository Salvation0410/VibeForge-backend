# AI 服务 Windows 本地启动方案设计

## 目标

更新 `ai-service/README.md` 的本地启动章节，使 Windows PowerShell 用户在 uv 默认 Python 管理链接损坏时仍能可靠完成初始化和启动，同时保留环境正常时的简化命令。

## 方案

本地启动文档分为两条路径：

1. 推荐的可靠流程将 CPython 3.12.14 安装到 `%LOCALAPPDATA%/yu-ai-code-mother/python`。初始化时从实际补丁版本目录定位 `python.exe`，用它创建项目 `.venv`、同步锁定依赖，并直接启动 Uvicorn。该流程不依赖 uv 默认的 `%APPDATA%/uv/python` minor-version Junction 或 trampoline。
2. 简化流程保留 `uv sync --frozen --python 3.12` 和 `uv run uvicorn`，仅用于 uv 托管 Python 链接工作正常的环境。

首次初始化和后续日常启动分开说明。日常启动重新发现 `%LOCALAPPDATA%` 中的真实解释器，并通过 `PYTHONPATH` 加载 `.venv/Lib/site-packages` 和项目 `src`，避免调用可能损坏的 `.venv/Scripts/python.exe` 启动器。

## 错误处理

- 每个初始化步骤检查 `$LASTEXITCODE`，失败时立即终止并给出明确阶段名称。
- 如果出现 `No Python at ...`、路径前含异常引号或 uv 报 minor version link 缺失，用户切换到推荐流程，不重复执行简化流程。
- 命令使用正斜杠和自动路径发现，避免要求用户手工输入包含 `x86_64` 的长路径。
- 模块名使用正常的 `ai_service.app:create_app`，明确不得写成 `ai\_service.app:create\_app`。

## 文档范围

- 修改 `ai-service/README.md` 的“本地启动”章节。
- 增加首次可靠初始化、日常启动、简化方案和 `No Python at` 排障说明。
- 保留现有 `.env`、Spring Boot、Docker、健康检查和测试章节结构。
- 不修改 Python/Spring 业务代码，不提交本地 Python 运行时或 `.venv`。

## 验证

- 验证可靠初始化命令能够定位并运行 CPython 3.12.14。
- 验证 Uvicorn 能加载 `ai_service.app:create_app` 并完成应用启动。
- 验证 `GET /health/live` 返回 `{"status":"live"}`。
- 运行 `git diff --check` 检查 Markdown 格式。
- 检查 Git 状态，确认没有加入 `.venv`、本地 Python 运行时或其他生成文件。
