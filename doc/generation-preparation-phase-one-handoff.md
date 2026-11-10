# 生成前需求整理与前端辅助规则：第一期交接

后续进展：本文件保留第一期交付记录；2026-11-10 增加的诊断、评估与真实六次重写结果见 [评估与诊断交接](generation-preparation-evaluation-handoff.md)。

日期：2026-11-10。范围：LangGraph 生成链路的 HTML、MULTI_FILE、VUE_PROJECT，以及 Spring 阶段展示适配。用户已选择后台自动整理后生成，不增加确认流程。

## 1. 行为与范围

原始用户需求仍是生成及审查依据；新增结构化说明帮助模型区分明确需求、保留约束、设计建议、假设、交互和验收条件。内置前端规则提供信息层次、可访问性、技术栈和产品类型的指导。

本期使用人工编写、按项目技术限制适配的中文规则。来源链接说明设计参考，规则版本为本项目维护版本；没有安装完整第三方技能，没有复制其脚本或工具权限，也不在生成时联网拉取技能。

没有修改前端仓库、Legacy 引擎、模型生成类型路由、数据库表、工具契约、生成任务仲裁、发布指针或自动恢复策略。既有 Vue 工程直接写入和缺少版本回滚的限制仍然存在。当前没有视觉截图审查，不能将本轮离线测试视为视觉提升或真实端到端验收。

## 2. 调用链与文件

```text
input_guard -> context_prepare -> generation_prepare
  -> html_generation / multi_file_generation / vue_generation
  -> verification_repair（硬校验、Vue 构建、质量检查、有限修复）
  -> artifact_publish（静态类型）/ finalize
```

| 文件 | 职责 |
| --- | --- |
| `ai-service/src/ai_service/models/generation_brief.py` | 严格摘要契约、有界重写输入、原文引用检查 |
| `ai-service/src/ai_service/prompts/preparation.py` | 需求整理的固定 JSON 协议与意图保护规则 |
| `ai-service/src/ai_service/prompts/frontend_skills.py` | 本地规则、来源、版本、选择和可信系统指令组装 |
| `ai-service/src/ai_service/orchestration/nodes/preparation.py` | 单次整理、超时、降级与请求内上下文保存 |
| `ai-service/src/ai_service/models/base.py` | `rewrite_requirements` 模型协议 |
| `ai-service/src/ai_service/models/openai_compatible.py` | 一次供应商调用及所有生成/审查/修复入口的规则注入 |
| `ai-service/src/ai_service/orchestration/workflow.py` | 主图接入准备节点 |
| `ai-service/src/ai_service/orchestration/nodes/verification.py` | 多角色审查也接收相同准备结果 |
| `src/main/java/com/yupi/yuaicodemother/ai/gateway/LangGraphAiGenerationGateway.java` | 将准备状态适配成现有展示协议 |

## 3. 规则选择与维护

共同规则为 `design-core`、`accessibility`，再按类型选择 `static-native` 或 `vue-native`。创建/修改模式以 Spring 返回的 `currentArtifact.exists` 为准，不能由模型自行修改。

- 已有项目选择 `preserve-style`，保留未涉及部分；用户明确要求的全局改版仍可执行。
- 新建项目包含数据工具关键词时选择 `product-dashboard`，商品关键词选择 `product-commerce`，其余选择 `product-content`；匹配顺序见选择函数，每次最多一个产品预设。
- 三类生成技术协议优先，用户明确要求优先于默认设计规则和模型建议。静态类型仍禁止外部脚本、字体与样式依赖；Vue 沿用现有 JavaScript、Vue 和原生 CSS，不默认引入 React、TypeScript 或 Tailwind。

参考来源：

- [Anthropic frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design)
- [Vercel web-design-guidelines](https://github.com/vercel-labs/agent-skills/tree/main/skills/web-design-guidelines)
- [vuejs-ai vue-best-practices](https://github.com/vuejs-ai/skills/tree/main/skills/vue-best-practices)
- [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)

修改规则时增加对应本地版本并运行选择/传播测试。系统指令只解析白名单 ID 与匹配版本，不读取上下文中的任意规则正文。更新服务前应排空正在执行的请求，避免进程替换中途丢失执行；本期没有增加自动续跑能力。

## 4. 重写输入与输出

输入包括当前完整原文、最近最多六条用户需求（每条最多 1000 UTF-16 单元）、最多 2000 UTF-16 单元的初始需求、当前类型、创建/修改模式与已选规则引用。重写不读取源码或执行文件工具；它不知道未提供的工程视觉细节。后续 Vue 修改仍须按既有规则读取目标源码。

摘要字段：

| 字段 | 校验与含义 |
| --- | --- |
| `requirements` | 1～12 条原文摘录，每条不超过 400 字符 |
| `preserve_constraints` | 最多 12 条保留要求的原文摘录 |
| `design_brief` | 最多 800 字符的辅助设计建议 |
| `assumptions` | 最多 5 条假设，每条不超过 400 字符 |
| `interactions`、`acceptance` | 各最多 8 项，包含 `description` 与原文 `evidence` |

不接受额外字段或宽松类型转换。摘录和证据必须出现在重写收到的原文来源中；这一检查只保证引用存在，不能证明描述没有曲解引用、没有遗漏需求或建议一定合理。因此后续模型仍同时接收原文，质量审查不能只审摘要。

`context.generationPreparation` 记录本地 schema `version`、`mode`、规则 `id/version`、`brief` 和 `rewriteStatus`。生成、静态流式生成、单 Reviewer、多 Reviewer、静态修复与 Vue 工具修复共用这份准备数据。子图继续使用既有窄 schema，没有恢复完整父状态透传。数据仅服务当前请求，不是跨请求长期记忆。

## 5. 配置与降级

| 环境变量 | 默认值 | 作用 |
| --- | --- | --- |
| `AI_SERVICE_FRONTEND_SKILLS_ENABLED` | `true` | 启用本地前端规则 |
| `AI_SERVICE_PROMPT_REWRITE_ENABLED` | `true` | 启用后台结构化需求整理 |
| `AI_SERVICE_PROMPT_REWRITE_TIMEOUT_SECONDS` | `15` | 单次整理超时，范围大于 0 且不超过 60 秒 |
| `AI_SERVICE_PROMPT_REWRITE_MAX_TOKENS` | `1500` | 输出预算，允许 128～4096 |
| `AI_SERVICE_PROMPT_REWRITE_MAX_BYTES` | `8192` | UTF-8 字节上限，允许 256～16384 |

配置从现有 Settings 加载，修改后重启 Python 生效；`.env.example` 已同步，已有 `.env` 不自动覆盖。重写复用当前模型和异步 HTTP 客户端，对该次调用显式关闭 SDK 重试，不单独配置供应商或创建公开接口。

输出额度与上下文窗口的关系在实际模型调用前检查；如重写输出预留加协议开销已无法容纳输入，会按硬预算错误停止，不能依靠普通降级绕过。

- `ready`：成功取得并校验摘要。
- `fallback`：超时、供应商错误、无效/截断输出、无效引用或摘要超限；不使用坏摘要，继续原文和已选规则。
- `disabled`：关闭重写，仍可加载规则。
- 两开关都关闭：不调用重写、不附加准备上下文和规则；主图中的节点仍存在。
- `GenerationCancelled`、任务取消和 `InputReviewError`（包括硬预算错误）不会被降级吞掉。输入审核仍在此前执行。

重写、生成、审查和修复均服从现有完整消息预算。新摘要不挤占后通过裁剪原文来“放行”；Vue 工具历史仍限制为 24 KiB，模型工具总预算默认 40、有限修复默认 2 次。没有新增额外设计修复循环。

## 6. 展示与可观测边界

准备节点发送固定短文案，失败时提示将按原始需求继续。模型返回正文、摘要、来源内容和凭据不进入进度事件或异常日志。

Spring 精确处理 `node_status / generation_prepare / progress`：静态类型使用 `StaticGenerationStreamFrame.display`，Vue 使用既有 `ai_response`。公开 SSE 协议不变。准备消息不会进入静态最终源码历史；仅真实 `content_delta/display_delta` 标记新草稿并使旧候选失效，迟到准备消息不清除完整候选。候选仍只在唯一成功终态后交给下游。

规则版本和整理状态存在请求图状态中，正常终态沿用现有 checkpoint 清理；没有新增长期报表、后台查询接口或自动 A/B 实验平台。

## 7. 验证与交付记录

离线用例位于 `ai-service/tests/test_generation_preparation.py`，Java 回归位于 `LangGraphAiGenerationGatewayTest`，继续复用 `SimpleTextStreamHandlerTest`。

本轮基线 `uv run pytest`：869 通过、10 跳过、1 失败；失败为既有 `test_spawn_worker_cancelled_request_keeps_ipc_lock_until_shutdown` 的进程取消锁时序断言。原样单独复跑通过（1 passed），未修改重排器实现或测试。

最终代码的验证结果：

| 命令 | 本轮结果 |
| --- | --- |
| `uv run pytest`（`ai-service`） | **945 passed、10 skipped**，129.74 秒；两条既有依赖弃用警告 |
| `uv run python -m compileall -q src`（`ai-service`） | 退出 0 |
| `uv lock --check`（`ai-service`） | 退出 0，150 个依赖包，无锁文件修改 |
| `mvn test '-Dtest=LangGraphAiGenerationGatewayTest,SimpleTextStreamHandlerTest'` | **26 项通过** |
| `mvn clean -DskipTests compile` | 退出 0 |
| `git diff --check` | 无空白错误 |

旧行为测试夹具显式关闭两项增强，以固定既有契约；新增专项覆盖默认开启、两项独立开关、三类型创建/修改、严格摘要、SDK 禁重试、取消/预算、修复和多角色传播。全量验证已包含新增专项，不将只跑旧夹具的结果当作增强能力验证。

Java 新增测试先复现准备状态被忽略，再完成实现。`mvn test '-Dtest=LangGraphAiGenerationGatewayTest,SimpleTextStreamHandlerTest'` 已通过 26 项；`mvn clean -DskipTests compile` 退出 0。仓库当前未提供 `mvnw.cmd`，该命令无法执行，因此本轮使用本机 `mvn`。

第一次改造后全量测试还发现双开关关闭时多角色上下文多出历史字段（2 failed、942 passed、10 skipped），已收紧复制条件并保留旧测试严格断言。后续同时收紧开启时的用户历史投影，只传与重写相同的有界用户原文，避免助手消息或超长历史进入多角色审查。

未执行真实模型、浏览器端到端、多模态视觉比较或显式 opt-in 的 PostgreSQL/MySQL/Redis 资源测试。离线结果不代表最终视觉质量已被量化验证。

## 8. 启用、回退与后续验收

部署配套 Spring/Python 版本并重启后，两项增强默认开启。无需 SQL 迁移或新增前端依赖。旧 Spring 可能忽略静态准备消息，因此推荐同时更新网关。

需要回退增强时，将两开关设为 `false` 并重启 Python；只需停用模型重写时单独关闭 `PROMPT_REWRITE_ENABLED`。不要为了回退本功能修改发布数据库指针、代际或删除项目目录。

下一步效果验收应在隔离应用上，以相同模型配置对比开关前后：三类首次生成、局部样式修改、明确全局改版、禁止新增图片等场景，记录需求保留情况、构建成功率、修复次数、耗时、模型消耗以及桌面/移动端截图盲评。第二期才考虑经过 Spring 工具边界的截图采集与有证据的视觉反馈，不承诺首期已经具备该能力。
