# 生成前需求整理与前端规则第一期实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. 按任务实现、验证和复核；用户已批准方案并要求在当前 dev 工作区实施。

**Goal:** 三类 LangGraph 生成自动整理用户需求、按需加载适配前端规则，并贯穿生成、审查和有限修复，交付运行与维护交接文档。

**Architecture:** 输入审核通过、当前上下文准备完成后执行独立准备节点。原始 prompt 不变；结构化说明作为低于用户明确需求的补充数据；服务内置版本化规则进入模型系统指令。重写故障有限降级，取消和硬预算错误不得吞掉。

**Tech Stack:** Python 3.12、LangGraph、LangChain ChatOpenAI、Pydantic、Spring Boot Java 21。

## 任务一：Python 准备能力及闭环

- [x] 新增 `models/generation_brief.py` 严格需求说明契约、`prompts/preparation.py` 重写指令、内置前端规则资源/选择器，以及 `orchestration/nodes/preparation.py`。
- [x] `config.py` 增加独立规则/重写开关、重写超时、输出预算、说明大小上限；默认启用，禁止重写 SDK 隐式重试导致无界调用。
- [x] `GenerationModel` 与适配器增加 `rewrite_requirements(context)`；使用现有异步模型与严格 JSON 校验，保留响应完整性和真实消息预算检查。
- [x] 主图接入 `context_prepare -> generation_prepare -> 三类生成`；仅经现有 context 传入子图，不传完整父状态。
- [x] 规则根据真实 `currentArtifact.exists`、生成类型和产品关键词选择；现有项目采用保守保留规则；首次生成最多一个产品预设。规则自主编写并注明参考来源，不执行外部 Skill 脚本。
- [x] 原文、类型、历史/图片语义不被重写覆盖；需求说明区分明确需求、假设与验收要求。失败/超时/格式错误降级为原文和基础规则；取消、输入审核错误、硬预算异常继续失败。
- [x] 静态流式与非流式生成、Vue、单/多 Reviewer 及修复均收到相同准备约束；纯审美偏好不新增无界修复循环。
- [x] 新增离线测试覆盖三种类型、创建/修改选择、严格输出、截断、超时、取消、预算、关闭开关、修复和多 Reviewer 传播；保留既有测试约束。

验证命令（`ai-service`）：`uv run pytest tests/test_generation_preparation.py`，然后 `uv run python -m compileall -q src`、`uv run pytest`、`uv lock --check`。

## 任务二：Spring 阶段展示兼容

- [x] 在 `LangGraphAiGenerationGateway` 对 `generation_prepare` 的受控进度消息进行三类型转换。静态类型使用 `StaticGenerationStreamFrame.display`，Vue 沿用现有 `ai_response`，不新增 SSE 协议。
- [x] 增加 `LangGraphAiGenerationGatewayTest` 回归，验证准备状态不会污染候选与发布内容，原始完成/失败行为不变。

验证命令：`mvn test '-Dtest=LangGraphAiGenerationGatewayTest,SimpleTextStreamHandlerTest'`、`mvn clean -DskipTests compile`。已核对当前仓库没有 `mvnw.cmd`，使用本机 Maven。

## 任务三：交接与交付

- [x] 更新 `ai-service/README.md`、`.env.example`、根 `AGENTS.md` 对应当前行为。
- [x] 新增 `doc/generation-preparation-phase-one-handoff.md`，包含节点、规则来源/版本、配置、降级、兼容性、验证证据、回滚方式和第二期边界。
- [x] 独立规格复核通过后进行质量复核，修复实际问题；运行最终验证及 `git diff --check`。
- [x] 检查 `git status --short`、上一条提交信息，仅提交本任务文件，中文 Conventional Commits。

## 明确不包含

不安装或运行第三方 Skill CLI，不添加任意脚本执行能力；不增加视觉截图审查、不自动恢复 checkpoint、不改 Legacy、不改前端生成窗口/刷新逻辑、不新增数据库表、不访问真实模型或自动开启真实资源测试。
