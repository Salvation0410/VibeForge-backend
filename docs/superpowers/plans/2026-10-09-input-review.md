# 输入审核实施计划

> 在当前会话按步骤实施，保留工作区已有修改，分别提交相关仓库。

**目标：** 输入审核覆盖安全、范围、需求质量与预算，普通模糊需求继续生成。

**架构：** 严格审核结果模型、独立审核编排服务、模型适配器及预算检查；路由与生成复用，Java 补充上下文并映射现有协议。

**技术栈：** Pydantic、LangChain、LangGraph、FastAPI、Spring Boot、Vue。

1. 新增 `models/input_review.py`、`prompts/input_review.py`、`orchestration/input_review.py`，在 `config.py` 增加审核和预算配置。测试四决策一致性、否定语义提示、控制字符、长度、超时、重试、取消与历史限额。
2. 在 `models/base.py`、`models/openai_compatible.py` 加审核模型协议、严格 JSON 解析和真实请求预算检查。运行 `uv run pytest tests/test_input_review.py tests/test_openai_compatible.py`，确认截断/不合法结构不能放行。
3. `workflow.py` 的 input_guard 调用审核服务；`api/routes.py` 的 route 在路由前调用同一服务。生成返回中文 failed，警告用独立 node_status。新增 API 测试断言拦截后未调用任何 Spring 工具，ALLOW/WARNING 原提示不变。
4. `LangGraphAiGenerationGateway.java` 补充有界业务上下文，映射结构化路由错误及三类审核警告。更新 Java 回归测试；前端按审核错误码恢复输入、显示提示，保留已有预览失败语义。
5. 同步 README、`.env.example` 和联调文档。运行 compileall、Python 全量 pytest、uv lock --check、Java 干净编译和相关测试；涉及前端运行既有流式测试、type-check、build-only。仅暂存本次变更，检查 diff --check，以中文 Conventional Commits 提交并报告实际验证范围。
