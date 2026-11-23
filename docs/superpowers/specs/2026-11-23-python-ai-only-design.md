# Python AI 专用完整项目

用户已确认：在 D:/VibeForge/yu-ai-code-mother-pythonAIOnly-version 中保留完整业务项目，所有 AI 执行由 Python 服务负责，清除 Java AI 框架旧链路。原项目及独立前端保持原状。

## 架构与边界

Spring 保留权限、MySQL、任务台账、Redis 幂等、文件沙箱、构建、静态发布、部署和 SSE；唯一 AiGenerationGateway 实现通过 HTTP 调用 Python。Python 保留现有 LangChain/LangGraph、输入审核、三种代码生成、有限修复、取消、checkpoint 以及客服知识处理。

移除 Legacy/灰度委派、Java 模型工厂、框架覆盖源码、Java 图实验、Java 模型配置、框架依赖、无调用者的旧提示词及专属测试。共用图片搜索迁入业务工具包，保留 Python 工具协议。聊天历史与 SSE 数据结构解除框架类型依赖；不改变公共 SSE 字段或静态展示/候选隔离。

## 失败与验证

Python 不可用时传播既有服务错误，不回退 Java 模型。保留静态发布仲裁和取消语义。以 Maven 编译、离线 Java 回归、Python Fake Model 全套测试、依赖与源码残留扫描验证；需要真实数据库、OSS、浏览器或模型的检查单独报告，不声称运行过。

## 迁移方式

从当前源仓库创建独立本地克隆和 codex/python-ai-only 分支，保留 Git 历史便于对比；不复制密钥、生成产物、日志或虚拟环境。更新根 README、AGENTS.md 和 AI README，历史设计文档标明仅作存档。最终提交只在目标仓库。
