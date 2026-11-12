# Vue 修复阶段信息与反馈隔离

## 证据与范围

2026-11-12 的单次自然生成触发 `VUE_COMPONENT_CONTRACT_FAILED`，进入第一次修复后耗尽共享工具预算，以 `VUE_TOOL_CALL_LIMIT_EXCEEDED` 失败，未重新构建。原始失败证据保留在 ignored 的 `tmp/vue-functional-20261112/`，不能改记通过。

旁路模型输入确认：生成时的 `currentArtifact.exists=false` 延续至修复；修复子图重新初始化空 filePlan 后，仍据此向模型发送 `phase=plan_files`，并带有上一生成阶段的 planningFeedback、vueFormatFeedback。这是可确定复现的阶段信息错误；模型反复读写还可能受其他因素影响，不能断言它是本次失败的唯一原因。

选择纠正阶段信息及反馈生命周期，不扩大工具预算、不新增工具内去重或浏览器生产门禁。后两种方案会改变更大的控制流，当前没有必要。

## 行为

1. Vue 工具循环在没有本阶段 filePlan 时，`node=repair` 使用 `modify_files`，与初次请求的 currentArtifact.exists 无关；原始基线不伪造为存在。有本阶段新 filePlan 时仍沿用 write_files 及 pendingFiles 约束。
2. 新修复子图初始化时移除上一阶段的 planningFeedback、batchRetryFeedback、vueFormatFeedback。生成流程保持原行为；本修复阶段新产生的反馈仍按原机制传给后续模型轮次。恢复已有子图不重新初始化，因此不清除当前阶段已保存的反馈。
3. 保留原文、准备结果、完整工具历史、构建/校验诊断、修复轮次和累计工具预算。工具能力、两次修复上限、40 次共享工具预算、24 KiB 模型历史投影与 SSE 协议不变。

## 验证与结论边界

受控子图回归覆盖初次/已有产物、首次/第二次修复，验证初始阶段、旧反馈清除、新格式反馈保留、历史和预算连续。旧代码应失败，修复后通过。执行 Python 全量、compileall 和锁文件检查；现有父图构建失败回归继续确认重新构建与有限修复路由。

本轮原始自然失败不会重发。若另做该隔离应用的普通修复请求，必须使用新 requestId、新独占标记和原模型账本，单独报告；不能当作原失败请求续跑或修复前后受控对照。未达到第二次构建和浏览器检查的原请求不能称自然闭环通过。
