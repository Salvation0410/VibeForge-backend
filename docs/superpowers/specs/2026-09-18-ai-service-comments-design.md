# AI 服务中文注释设计

## 目标

为 `ai-service` 的关键公开方法、协议边界和 LangGraph 编排逻辑补充必要的中文说明，使维护者无需通读实现即可理解职责、输入输出、副作用和失败边界；同时保持启动文档以中文为主，不改变任何运行行为。

## 注释范围

注释采用 Python docstring，覆盖以下边界：

- `app.py`：应用工厂、生命周期、内部鉴权、健康检查、路由、流式生成和取消接口。
- `workflow.py`：工作流入口、流式执行、图构建、工具调用、取消检查和 checkpoint 摘要。
- `llm.py`：模型协议、OpenAI 兼容适配器、路由、生成、质量检查和修复。
- `checkpoint.py`：checkpoint 协议、禁用实现、Redis LangGraph saver 和 Redis 生命周期封装。
- `tools.py`：Spring 工具网关调用与资源释放。
- `events.py`、`cancellation.py`：事件序列和协作式取消语义。
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

## 验证

- 运行 `uv run pytest`，确认注释修改没有改变行为。
- 运行 `uv lock --check`，确认锁文件有效。
- 运行 `python -m compileall src`，确认 docstring 与源码语法正确。
- 运行 `git diff --check` 并人工检查差异只包含注释和文档表达。

## 非目标

- 不调整工作流节点、提示词、模型配置或接口协议。
- 不重构类和方法结构。
- 不给每个私有辅助函数添加模板化注释。
- 不修改 Spring Boot 代码。
