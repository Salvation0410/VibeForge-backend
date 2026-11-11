# 保守重写策略与独立样本交接

日期：2026-11-10。承接 [准备评估交接](generation-preparation-evaluation-handoff.md)。本轮用户选择“优化重写策略与独立样本”，在当前 dev 工作区实施；本轮真实模型调用为 **0**，没有重复使用上一轮已经耗尽的六次额度。

## 1. 交付结论

新增可选 `conservative` 策略与纯离线分类评估工具。默认仍是 `always`，现有请求行为不会因升级自动改变。

**当前独立证据没有证明可以减少调用。** 最终独立集 24 条中：9 条语义上可简化的单项修改全部漏识别，15 条应重写的需求都保留重写，预计调用数仍为 24。跳过精确率为 null，而不是 100%；未产生跳过不能证明策略在实际流量中安全或有效。开发集可命中的表达只能说明功能路径可用，不能代表泛化收益。

因此本轮交付的是可回退的实验能力、明确的负面验证结果与后续依据，没有将该策略默认推广，也没有声称减少了真实生成时间、成本或改善页面效果。

## 2. 已实现行为

| 配置 | 行为 |
| --- | --- |
| `AI_SERVICE_PROMPT_REWRITE_ENABLED=false` | 沿用关闭重写逻辑，标为 disabled |
| 重写开启且 `AI_SERVICE_PROMPT_REWRITE_POLICY=always` | 默认；继续一次有界辅助重写 |
| 重写开启且 `AI_SERVICE_PROMPT_REWRITE_POLICY=conservative` | 完整识别的简单修改可跳过，否则继续重写 |

当前策略版本为 `2`，只接受 `currentArtifact.exists` 严格为 true、最长 240 字符、非多行、完整匹配的请求。支持有限颜色、尺寸属性，以及带原文和新文案的引号文字替换；目标使用常见元素词组、位置词和带引号名称的正向识别，同时保守拒绝指代、否定、全局范围、多元素和复合操作。

例如“只把预约按钮改成绿色，保留文字、图片和其他操作。”可以在 conservative 下省去辅助重写。开发用例中还覆盖字号、边距及明确的按钮文案替换。属性、目标或附加约束不在支持语法中时继续模型重写，不丢弃未识别的部分。

跳过时 `context.generationPreparation.rewriteStatus=skipped`、`brief=null`；独立诊断为 `skipped/SIMPLE_EDIT`、`modelAttempted=false`、`briefBytes=0`。跳过不计为重写成功，不属于供应商故障降级；用户原文、有限历史、原始保留约束、已选规则继续进入生成。关闭诊断不关闭策略，关闭技能不自动关闭重写。

本轮只在既有准备节点内增加门控，未移动图节点或更改子图 schema：输入审核、后续模型消息预算、生成/修复、文件权限、工具预算与公共 SSE 保持原有边界。取消继续向上抛出。缓存重入沿用既有准备结果，切换配置不会在同一个已准备请求中再次调用模型。

该识别器是有限语法规则，不是完整语义解析器；无法承诺任意复杂表达都分类正确。上次 15 秒超时不足以支持放宽超时或增加重试，本轮没有更改这两项行为。

## 3. 代码入口

| 路径（相对根目录） | 用途 |
| --- | --- |
| `ai-service/src/ai_service/models/rewrite_policy.py` | 版本化纯函数策略，无模型、网络或文件副作用 |
| `ai-service/src/ai_service/config.py` | 新 policy 枚举，默认 always |
| `ai-service/src/ai_service/orchestration/nodes/preparation.py` | 策略接入、skip 语义、缓存重建 |
| `ai-service/src/ai_service/models/preparation_diagnostics.py` | skipped/SIMPLE_EDIT 固定枚举扩展，复用现有 trace/checkpoint 白名单 |
| `ai-service/src/ai_service/orchestration/rewrite_policy_evaluation.py` | 严格数据集、混淆矩阵、分组汇总、仅离线 CLI |
| `ai-service/tests/test_rewrite_policy.py` | 开关/三分支/取消/缓存/原文保护、规则负例、指标/隐私测试 |
| `ai-service/evaluation/rewrite-policy-development-v1.json` | 开发集 16 条，6 可跳过 / 10 应重写 |
| `ai-service/evaluation/rewrite-policy-holdout-v1.json` | 首轮独立集，之后已用于诊断，不再作为新版独立证据 |
| `ai-service/evaluation/rewrite-policy-holdout-v2.json` | 最终独立集 24 条，9 可跳过 / 15 应重写 |

## 4. 评估方法与结果

样本由未查看策略实现及开发测试的协作者独立编写，三种生成类型各 8 条。首版固定目标名策略在第一批独立集识别 0/9，因此保留首份报告，将该集转为诊断参考，并准备一批全新的未见样本。第二版先完成规格复核及开发测试，再首次执行第二批；读取其失败样本后未再修改匹配规则。

第二版首次评估前的本机文件 SHA-256：

- 策略文件：`D3A46C6DB70732EB5E12940ABA4F55CC9EC887038BF0BC9208B1B68626DB7697`
- 第二批独立数据：`D8617BB1A844DC408F1C72501EEFF832D0364068618DCB5476CF218FE8BE1EC4`

以上为当时原始文件字节指纹，Git 换行转换可能改变原始文件哈希；报告另外保存规范化数据集 SHA-256 与策略版本。

| 评估 | 总数 | 正确跳过 | 误跳过 | 漏识别 | 正确保留重写 | 预计调用数 always → conservative |
| --- | --- | --- | --- | --- | --- | --- |
| 首版 / 首批独立 | 24 | 0 | 0 | 9 | 15 | 24 → 24 |
| 最终版 / 开发集 | 16 | 6 | 0 | 0 | 10 | 16 → 10 |
| 最终版 / 第二批独立 | 24 | 0 | 0 | 9 | 15 | 24 → 24 |

第二批漏识别涉及对齐、行高、字间距、分隔线样式、最大宽度、字重、不透明度，以及更自然的文字替换表达。它们表明支持语法和目标范围仍窄，不能用开发集 6 次预计省略来宣传生产收益。

指标以 `expectedSkip` 为人工语义标签：`falseSkip` 为应重写却预测跳过，`missedSkip` 为可跳过却仍预测重写。精确率分母为预测跳过数，召回率分母为应跳过数；空分母返回 null。`predictedAvoidedCalls` 包含误跳过，不能单独当作安全节省。报告分支和创建/编辑分组汇总，只写样本 ID、固定枚举、布尔标签与数字；不保存需求正文、凭据或模型输出。

本机报告位于忽略目录，未提交生成文件；核心数字已保存在本交接文档：

- `tmp/preparation-evaluation/policy-holdout-first.json`
- `tmp/preparation-evaluation/policy-development-final.json`
- `tmp/preparation-evaluation/policy-holdout-v2-first.json`

## 5. 运行与回退

在 `ai-service` 目录执行，输出父目录必须存在、文件必须尚不存在：

```powershell
uv run python -m ai_service.orchestration.rewrite_policy_evaluation --dataset evaluation/rewrite-policy-development-v1.json --output ../tmp/policy-development-new.json
uv run python -m ai_service.orchestration.rewrite_policy_evaluation --dataset evaluation/rewrite-policy-holdout-v2.json --output ../tmp/policy-holdout-new.json
```

此工具只评估分类，`actualModelCalls=0`；不加载 `.env`，不启动业务服务，不生成/发布应用，没有真实运行耗时指标。旧 `preparation_evaluation` 三组工具继续固定 always 行为，本轮未扩大其六次调用上限。

试用需显式设置 `AI_SERVICE_PROMPT_REWRITE_POLICY=conservative` 并重启 Python；恢复为 always 即回退。旧请求的缓存准备结果不改变，回退作用于新请求。无需 SQL 迁移、前端依赖或 Java 修改。本轮没有修改本机 `.env`。

## 6. 验证

| 命令/检查 | 本轮结果 |
| --- | --- |
| `uv run pytest`（ai-service） | **1073 passed、10 skipped**，163.22 秒；两条依赖弃用警告 |
| `uv run pytest tests/test_rewrite_policy.py -q` | 60 项专项通过 |
| `uv run python -m compileall -q src` | 退出 0 |
| `uv lock --check` | 退出 0，150 个依赖包，未修改锁文件 |
| 独立规格与质量审查 | 通过；复合/全局/含糊目标误跳过已补回归并收紧为正向识别 |
| `git diff --cached --check` | 通过 |

这些离线验证证明实现路径和已知边界，不替代真实模型生成质量验收。

本轮未进行真实模型、Spring/Python 联调、浏览器、构建、视觉盲评或 PostgreSQL/MySQL/Redis 资源验收；Java 和前端未改动，未运行 Maven 或前端构建。前端仓库已有的客服相关改动保持不动。

## 7. 下一步建议

不建议继续为了提高现有独立集分数而堆叠正则。若要让局部修改稳定减少准备调用，更值得单独设计“结构化编辑意图”：界面或可信服务明确传递目标、属性和新值，后端校验范围并保留原文；自由文本请求保持完整重写。该接口与 UI 尚未实现，需要另行明确产品入口和兼容行为。

2026-11-11 后续进展：现已复用可视化编辑入口实现四种单属性的显式意图，详情见 [结构化编辑交接](generation-structured-edit-handoff.md)。上段为本次策略评估结束时的建议，历史独立样本结果保持不变；新实现也尚无真实模型产物效果证据。

若仍研究自然语言门控，应先扩充单项属性、保留约束和指代的标注规范，使用新的独立集，再在隔离应用上对照真实生成、构建与功能保留。已有两批样本今后都属于已知数据，不能继续称为新策略的未见测试集。
