# AI 服务目录重构与中文注释设计

## 目标

将 `ai-service` 当前集中在单一目录的源码按职责拆分，并为关键公开方法、协议边界和 LangGraph 编排逻辑补充必要的中文说明。重构后维护者可以从目录结构识别组件边界，无需通读实现即可理解职责、输入输出、副作用和失败边界；启动方式、HTTP 契约和运行行为保持不变。

## 目标目录

```text
ai-service/src/ai_service/
├── __init__.py
├── app.py
├── config.py
├── api/
│   ├── __init__.py
│   ├── dependencies.py
│   ├── routes.py
│   └── schemas.py
├── orchestration/
│   ├── __init__.py
│   ├── workflow.py
│   ├── events.py
│   └── cancellation.py
├── models/
│   ├── __init__.py
│   ├── base.py
│   └── openai_compatible.py
└── infrastructure/
    ├── __init__.py
    ├── checkpoint.py
    └── spring_tools.py
```

文件职责如下：

- `app.py` 只负责组装依赖、创建 FastAPI 应用和注册路由，继续暴露 `create_app`。
- `config.py` 保留集中配置加载，避免各层自行读取环境变量。
- `api/schemas.py` 承载 HTTP 请求、响应和流事件数据结构；`dependencies.py` 提供内部鉴权依赖；`routes.py` 定义接口。
- `orchestration/` 承载 LangGraph 工作流、事件序列和协作式取消，不直接读取环境变量。
- `models/` 定义模型调用协议、公共返回类型和 OpenAI 兼容实现。
- `infrastructure/` 封装 Redis checkpoint 和 Spring HTTP 工具网关。

依赖方向为 `api -> orchestration -> models/infrastructure`。`app.py` 是组合根，可以引用所有包；基础设施层不得反向引用 API 路由。为控制迁移风险，本次不拆分 `workflow.py` 内部节点函数。

## 注释范围

注释采用 Python docstring，覆盖以下边界：

- `app.py`、`api/`：应用工厂、生命周期、内部鉴权、健康检查、路由、流式生成和取消接口。
- `orchestration/workflow.py`：工作流入口、流式执行、图构建、工具调用、取消检查和 checkpoint 摘要。
- `models/`：模型协议、OpenAI 兼容适配器、路由、生成、质量检查和修复。
- `infrastructure/checkpoint.py`：checkpoint 协议、禁用实现、Redis LangGraph saver 和 Redis 生命周期封装。
- `infrastructure/spring_tools.py`：Spring 工具网关调用与资源释放。
- `orchestration/events.py`、`orchestration/cancellation.py`：事件序列和协作式取消语义。
- `config.py`：配置加载入口。

简单数据模型、字段声明、显而易见的构造函数和只做字符串转换的辅助函数不逐一添加注释，避免重复代码本身已经表达的信息。

## 注释内容规范

- 使用简洁中文，技术名词如 LangGraph、checkpoint、NDJSON、Bearer 保留英文。
- 类 docstring 说明组件职责和边界。
- 关键方法 docstring 说明行为、关键参数、返回值以及需要关注的副作用或异常。
- 复杂图构建逻辑仅在分支、回环上限和外部工具边界处保留行内注释。
- 注释不承诺实现中不存在的行为，也不记录容易过时的版本号和部署地址。

## 启动文档

`doc/ai-service-startup.md` 保持中文章节和中文解释，命令、环境变量、HTTP 路径及产品名称保留原文。补充术语含义时优先在首次出现处解释，不翻译配置键名。

启动命令继续使用：

```powershell
uv run uvicorn ai_service.app:create_app --factory --host 0.0.0.0 --port 8000
```

## 验证

- 运行 `uv run pytest`，确认注释修改没有改变行为。
- 运行 `uv lock --check`，确认锁文件有效。
- 运行 `python -m compileall src`，确认 docstring 与源码语法正确。
- 搜索旧模块路径，确认源码和测试中不存在 `ai_service.workflow`、`ai_service.llm`、`ai_service.models`、`ai_service.checkpoint`、`ai_service.tools`、`ai_service.events`、`ai_service.cancellation` 的遗留 import。
- 保持现有 API 测试、工作流分支、修复上限、取消、Redis 降级和 Spring 工具网关测试全部通过。
- 运行 `git diff --check` 并人工检查差异只包含注释和文档表达。

## 非目标

- 不调整工作流节点、提示词、模型配置或接口协议。
- 除移动文件、提取 API 路由注册和修正 import 外，不重构类和方法内部逻辑。
- 不给每个私有辅助函数添加模板化注释。
- 不修改 Spring Boot 代码。
