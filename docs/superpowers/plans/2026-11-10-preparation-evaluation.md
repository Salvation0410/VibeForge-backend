# 需求准备效果评估与诊断实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. 用户选择效果评估与诊断，授权最多 6 次真实重写调用，沿用当前 dev 工作区。

**Goal:** 记录准备阶段耗时和稳定失败原因，提供三组可复现评估，并依据合成样本暴露的问题修正规则选择。

**Architecture:** 生产准备节点增加脱敏诊断，保持原有生成上下文和协议。评估复用实际准备节点，比较 baseline（关闭增强）、skills_only、enhanced；离线规则选择与真实重写指标分别标记，不用提示词指标宣称应用视觉提升。

**Tech Stack:** Python、LangChain、LangGraph、Pydantic、pytest；不新增依赖。

## 任务一：诊断

- [x] 增加稳定分类的摘要异常：超时、供应商错误、格式/协议错误、截断、引用错误、大小超限；取消和硬预算错误仍向上抛出。
- [x] 准备节点记录 `rewriteStatus/reason/durationMs/modelAttempted/briefBytes/skillIds`，写入独立 `preparation_diagnostics` 状态及脱敏日志；配置可关闭。
- [x] 不将诊断值混入模型 prompt；成功/降级沿窄状态传递，异常通过脱敏日志观测，不保存模型正文或供应商异常。
- [x] 复用现有 checkpoint 摘要和可选旁路追踪的白名单字段；诊断错误不能翻转生成终态。
- [x] 测试正常、禁用、降级分类、取消、预算、日志脱敏和故障隔离。

## 任务二：评估与证据驱动的选择优化

- [x] 新增版本化公开合成样本集，覆盖三类产物、创建/局部修改/全局改版、中英文产品类别及否定需求。
- [x] 新增严格样本校验和 CLI，默认离线；真实模式必须显式 `--real-model`，最多 6 个样本/6 次重写，SDK 无自动重试。
- [x] 三组公平使用同一输入，报告原文保留、规则匹配率、重写成功/降级率、稳定原因、耗时、摘要摘录覆盖；缺少摘要的指标用 null，不把缺失或假模型计为真实质量。
- [x] 先记录旧选择器在固定集上的错例，再修复明确否定词误命中与英文别名遗漏；保存选择器版本以便复现，规则正文不做无证据的审美调整。
- [x] 报告只输出样本 ID、摘要指标和汇总，不默认保存原文、摘要、源码、凭据、供应商 URL；不覆盖已有报告。
- [x] 调用用户授权的最多 6 次真实重写，结果失败也计入限额，不通过重复实验增加调用。

## 任务三：验证与交接

- [x] 运行相关测试、`uv run python -m compileall -q src`、`uv run pytest`、`uv lock --check`。
- [x] 更新 README、.env.example、AGENTS 与交接文档，区分历史第一期结果和本轮结果。
- [x] 更新计划状态，独立规格和质量复核，`git diff --check` 后中文 Conventional Commits 提交。

边界：未启动 Spring/Python 服务；不自动部署、不调用文件网关、不创建应用、不增加浏览器截图审查。真实调用仅限准备阶段，结果不能证明页面效果或端到端可用性。
