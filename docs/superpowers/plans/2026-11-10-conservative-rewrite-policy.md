# 保守重写策略与独立样本实施计划

> 使用 subagent-driven-development，在已授权的当前 dev 工作区完成实施与独立审查。用户已选择优化重写策略与独立样本；不重复消耗上轮六次真实调用额度。

**目标：** 为明确的已有页面单项修改提供可选零重写调用路径，以独立样本检验误跳过和漏识别，不宣称页面效果通过。

**设计：** `prompt_rewrite_policy=always|conservative` 默认 always。conservative 仅接受完整匹配的明确目标/值样式修改或明确引号文字替换，允许白名单保留约束；任意未识别部分、历史指代、新建、全局改版、多项操作均继续重写。策略不重写原文、不跳过输入审核/生成消息预算、不调整技能。策略跳过标记 skipped/SIMPLE_EDIT，取消照常传播，旧缓存不得重写。

**实现：** 独立纯函数策略模块 + 准备节点接入 + 固定诊断枚举扩展。保留现有三组重写评估及六调用上限；额外纯离线策略评估 CLI 比较 always 与 conservative 的预计调用数及误跳过、精确率、召回率，区分策略预测与真实模型/应用指标。独立样本由未读策略实现的协作者预先编写；首次评估发现零识别收益后，保留失败报告，将第一批转为诊断依据，并重新编写未见过的第二批，禁止把参与调整的旧集报告为新版独立验证。

## 任务

- [x] 固定策略边界与开发样本，在 `models/rewrite_policy.py` 实现 `should_skip_rewrite(context)`；只读输入，无供应商依赖。
- [x] 接入 config/preparation/diagnostics，缓存重入兼容，测试三类分支、开关、取消、保留原文与规则、复杂需求模型调用。
- [x] 独立生成 `evaluation/rewrite-policy-holdout-v1.json`，开发集独立保存，不把旧样本或实现反推集称为独立验证。
- [x] 新增 `orchestration/rewrite_policy_evaluation.py`：严格 schema、唯一 ID、分组矩阵、空分母 null、固定字段无原文报告、独占输出；默认且仅离线。
- [x] 固定实现后首次评估独立集，如实记录误跳过/漏识别，并决定是否仍维持默认 always。
- [x] 规格与质量审查，运行 compileall、全量 pytest、uv lock --check、diff --check。
- [x] 更新 README/.env.example/AGENTS/交接并提交中文 Conventional Commit。

验证重点：不能仅以短文本判定简单；不要正则搜索一段命中就忽略其余要求。旧节点 fallback/disabled 的语义保持，skipped 不等于重写成功，brief 必须为 null。所有修改仍受最终生成模型及工具边界控制。
