# 需求准备评估与诊断交接

后续记录：可选保守重写策略及新的独立样本结果见 [重写策略交接](generation-rewrite-policy-handoff.md)。该后续试验未证明独立集调用节省，默认仍保持 always。

日期：2026-11-10。承接 `generation-preparation-phase-one-handoff.md`，本轮在现有 `dev` 工作区实施，仅修改 Python AI 服务及相关文档。第一期历史验证数字不作为本轮结果。

## 1. 本轮交付

用户选择先补效果评估和诊断，再根据证据改进规则，并授权最多六次真实模型重写。本轮完成：

- 准备节点独立记录状态、稳定失败原因、耗时、模型尝试标志、摘要大小及规则 ID。
- 默认离线的三组对照工具与 12 条版本化公开合成需求，真实模式显式启用且最多六次调用。
- 修复否定词误命中产品类别、英文 shopping/admin panel 等别名遗漏；选择器版本升为 `2`，本地规则正文版本仍为 `1`。
- 已用满六次真实重写额度：五次成功，一次超时降级。未创建或发布应用。

本轮没有实现完整应用效果评估：未生成页面、截图、检查构建成功率或进行视觉盲评。规则匹配与摘要摘录覆盖只能验证准备阶段的有限指标。

## 2. 文件入口

| 文件（相对仓库根目录） | 用途 |
| --- | --- |
| `ai-service/src/ai_service/models/preparation_diagnostics.py` | 固定原因码、安全异常、观测字段校验和可信规则标签 |
| `ai-service/src/ai_service/models/generation_brief.py` | 严格摘要结构、引用来源与字节限制的分类失败 |
| `ai-service/src/ai_service/models/openai_compatible.py` | 非完整响应、JSON、结构、SDK 超时分类；重写 SDK 禁重试 |
| `ai-service/src/ai_service/orchestration/nodes/preparation.py` | 实际准备节点与成功/降级/失败/取消观测 |
| `ai-service/src/ai_service/orchestration/state.py`、`workflow.py` | 父状态字段及安全 checkpoint 摘要 |
| `ai-service/src/ai_service/infrastructure/langsmith_tracing.py` | 新增标量字段白名单 |
| `ai-service/src/ai_service/orchestration/preparation_evaluation.py` | 严格数据集校验、三组运行器、汇总与 CLI |
| `ai-service/src/ai_service/prompts/frontend_skills.py` | 选择器版本 2 |
| `ai-service/evaluation/generation-preparation-v1.json` | 三类生成、创建/修改、中英文、否定句固定样本 |
| `ai-service/tests/test_preparation_diagnostics.py`、`test_preparation_evaluation.py` | 注入模型/模拟 HTTP 回归，默认无外部调用 |

## 3. 诊断运行行为

新增 `AI_SERVICE_PREPARATION_DIAGNOSTICS_ENABLED=true`，独立于技能和重写开关。保持原文、生成类型、现有文件工具权限、窄子图状态和公共 SSE 契约。

诊断位于父图 `preparation_diagnostics`，包含：

| 字段 | 含义 |
| --- | --- |
| `rewriteStatus` | ready / fallback / disabled / failed / cancelled |
| `reason` | 固定原因码，无供应商异常正文 |
| `durationMs` | 节点准备逻辑耗时，不含后续生成或旁路上传 |
| `modelAttempted` | 是否进入本次模型尝试；发送前硬预算拒绝、缓存复用为 false |
| `briefBytes` | 合法摘要紧凑 JSON 的 UTF-8 字节数，无合法摘要为 0 |
| `skillIds` | 白名单规则的 `id@version`，有界去重 |

成功/禁用原因为 `NONE/DISABLED`。降级原因包括 `TIMEOUT`、`PROVIDER_ERROR`、`INVALID_JSON`、`INVALID_SCHEMA`、`INCOMPLETE_RESPONSE`、`INVALID_EVIDENCE`、`BRIEF_TOO_LARGE`。SDK 连接/读取超时与节点截止时间超时都归为 `TIMEOUT`。取消记为 `CANCELLED`；硬预算和输入失败记为 `BUDGET_EXCEEDED/INPUT_ERROR`，继续向上抛出，不能伪装降级成功。

成功和普通降级返回独立状态。异常路径只有日志/旁路观测，不声称节点状态已落盘。日志前缀为 `preparation_diagnostics`，启用原有 LangSmith 旁路时增加 `generation_preparation_diagnostics` span；只收集固定枚举、有限数字及可信规则标签。诊断不进入模型 `context`、子图或公开事件。观测故障不改变生成终态。

关闭诊断只停止新观测，不删除历史 checkpoint。完整 LangGraph checkpoint 本来就保存业务上下文和摘要，不等同于安全的摘要日志；本轮不改变其存储策略。缓存重入复用准备结果，不重复重写；诊断记录本次重入耗时及未调用标志。

## 4. 对照工具与指标解释

三组共用相同输入，每组深拷贝，防止上一组准备缓存影响下一组。固定输入含原文、生成类型、应用存在标志和空历史，不包含实际项目源码。

| 分组 | 技能 | 重写 |
| --- | --- | --- |
| baseline | 关闭 | 关闭 |
| skills_only | 开启 | 关闭 |
| enhanced | 开启 | 真实模式开启；离线标为 not_run |

默认离线不加载 `.env` 或创建模型。真实模式加载当前模型与重写预算配置，强制上述分组，禁用外部追踪；不调用输入审核、业务接口、数据库或项目网关。六次上限为每次 CLI 执行的保护，不能替代跨运行的用户授权总额度管理。

顶层 `rewriteAttempts` 计委托次数，失败/发送前硬预算拒绝也占额度。汇总内同名字段统计 `modelAttempted=true` 的样本；硬预算拒绝单独进入 `rewriteFailed`。成功/降级率基于已尝试样本；缺少摘要时摘录指标为 null，并给出测量分母。`sourceQuoteCoverage` 检查指定原文片段是否完整出现在 requirements、preserve_constraints 或 evidence 中，design_brief/描述/假设不能贡献分数。它不判断全部语义、冲突或遗漏。

报告包括数据集规范化内容 SHA-256、数据集/选择器/规则版本、模型配置指纹、时间与预算，不保存需求、摘要、源码、密钥或供应商 URL。模型指纹由端点及模型名散列得到；报告不能凭指纹复原运行环境。P95 采用最近秩法，六个样本的 P95 即最大值。耗时基于单调时钟，极短的离线准备可能显示 0ms，不代表没有运行开销。

报告先以独占方式创建，已存在文件直接拒绝且不会消耗模型调用。中断/配置错误可能留下空文件，不能覆盖重试；须先判断是否已经消耗授权次数。CLI 只给安全的通用错误提示，不打印 Pydantic 或供应商异常正文。

复现命令，在 `ai-service` 目录执行；输出父目录必须已存在：

```powershell
uv run python -m ai_service.orchestration.preparation_evaluation --dataset evaluation/generation-preparation-v1.json --output ../tmp/preparation-evaluation/new-offline.json
# 仅获得新的真实调用授权后执行，不重复消费本轮已用完的额度。
uv run python -m ai_service.orchestration.preparation_evaluation --dataset evaluation/generation-preparation-v1.json --output ../tmp/preparation-evaluation/new-real.json --real-model --limit 6
```

## 5. 本轮真实证据

数据集版本 `2026-11-10.1`，12 条合成需求。本轮先执行旧选择器，再实施修正；旧版 7/12 匹配，新版 12/12 匹配。修正样本为 coffee-no-admin、shopping-english、admin-english、portfolio-exclusions、commerce-no-admin。该样本集参与了修正规则，不能当作独立泛化测试集；另有四条转折、no-code 和英文词边界回归。

真实模型只评估数据集前六条，使用现有私有配置，重写超时 15 秒、输出上限 1500、摘要上限 8192 字节。共六次委托、无重试；其中五次 ready、一次 TIMEOUT fallback，成功率 83.3%，降级率 16.7%。三组共 18 次准备都保留原始输入；两组启用规则的选择均为 6/6。

| 样本 ID | 类型/场景 | 状态 | 准备耗时 | 摘录覆盖 |
| --- | --- | --- | --- | --- |
| coffee-no-admin | HTML 创建 | ready | 1.937 秒 | 100% |
| shopping-english | MULTI_FILE 创建 | ready | 2.328 秒 | 100% |
| admin-english | VUE_PROJECT 创建 | ready | 2.141 秒 | 100% |
| html-local-edit | HTML 局部修改 | TIMEOUT，回退原文 | 15.000 秒 | 未测量 |
| multi-global-restyle | MULTI_FILE 全局改版 | ready | 1.609 秒 | 100% |
| vue-dashboard | VUE_PROJECT 创建 | ready | 2.250 秒 | 100% |

六个 enhanced 样本耗时中位数 2.1955 秒，P95 为 15 秒。有效摘录覆盖均值 100%，分母仅五份成功摘要；超时样本没有按零分或成功摘要处理。没有为超时样本追加调用，不能从单个超时推断供应商根因、持续故障率或应扩大超时。

本机原始聚合报告（位于忽略目录，未提交生成文件）：

- `tmp/preparation-evaluation/selection-before.json`：修正前匹配证据。
- `tmp/preparation-evaluation/offline-final.json`：最终离线三组报告。
- `tmp/preparation-evaluation/real-six.json`：唯一真实六次批次报告。

本节表格保留核心结果，接手者无需依赖未提交的本机报告了解结论。

## 6. 验证与交付

最终验证结果：

| 命令/检查 | 本轮结果 |
| --- | --- |
| `uv run pytest`（ai-service） | **1013 passed、10 skipped**，139.10 秒；两条依赖弃用警告 |
| `uv run python -m compileall -q src` | 退出 0 |
| `uv lock --check` | 退出 0，150 个依赖包，未修改锁文件 |
| 离线三组评估 | 12 个样本、36 次准备、0 次重写调用 |
| 真实三组评估 | 6 个样本、18 次准备、6 次重写尝试，5 成功 / 1 超时降级 |
| 独立规格与质量审查 | 通过；SDK 超时分类问题已先复现再修复并回归 |
| `git diff --cached --check` | 通过，无空白错误 |

诊断专项覆盖默认开关、固定字段/原因、日志和追踪故障隔离、取消/预算传播、SDK 无重试、checkpoint 投影及模型/子图无诊断泄漏。评估专项覆盖严格数据集、离线零调用、三组隔离、调用上限、失败分母、摘录计分边界、报告隐私和文件冲突前置拒绝。中途全量 1010 项之后新增的三项回归已包含在最终 1013 项中。

本轮未修改 Java 或前端，所以未重跑 Maven、前端编译和浏览器端到端。Spring 8123 与 Python 8000 在检查时连接被拒绝；真实评估直接使用模型适配器，不代表这两个服务已运行。真实 PostgreSQL/MySQL/Redis 资源验收未启用。

## 7. 后续优化建议与回退

先收集更多有授权的准备阶段样本，重点验证局部修改的重写必要性与超时分布；不要根据六个样本直接增加重试或延长超时。选型规则仍是启发式，对跨分句否定、隐含转折、多产品混合需求不提供完整理解保证，应补独立保留集后再扩充规则。

下一步完整效果验收需在隔离应用上固定模型和需求，对三组执行真实生成、构建、修复和桌面/移动端截图，记录保留约束是否被破坏并盲评视觉质量；这不在本轮六次重写授权内，尚未实施。

关闭观测只需设置 `AI_SERVICE_PREPARATION_DIAGNOSTICS_ENABLED=false`；关闭重写可设 `AI_SERVICE_PROMPT_REWRITE_ENABLED=false`，规则仍可保留。恢复无增强输入需同时关闭 `AI_SERVICE_FRONTEND_SKILLS_ENABLED`，若也需原观测形状则一并关闭诊断，并重启 Python。无 SQL 迁移，不修改发布指针、代际、项目目录或前端 SSE。
