# 子图输入与私有状态收敛设计

本阶段执行交接文档第六节优先项二，用户已要求继续下一步优化。

## 方案

使用独立 TypedDict 输入、内部状态与输出 schema，继续直接注册编译子图。相比包装节点手工投影，此方案保持 LangGraph 子图发现、配置继承和恢复路径；相比截断工具历史，不改变生成与修复模型原有输入。schema 集中放在 `orchestration/subgraphs/schemas.py`，避免生成图、验证图和 Vue 图之间互相导入输出类型。

调用方注册子图时也显式指定 input_schema，先投影再调用，防止子图原始 `__start__` 检查点仍保存未过滤的父状态。阶段内部注册节点时显式指定所需 schema，避免函数类型注解隐式扩宽输入，或把修复所需的构建诊断和反馈过滤掉。

请求身份仅保留追踪与 Spring 工具需要的 app_id、request_id、code_gen_type；thread_id 已绑定在节点闭包与运行配置，无需子图状态再次复制。conversation、metadata、input_review、publish 由父图管理，不再以顶层字段进入子图。模型仍从 context 获取原有 conversation、metadata、图片和产物上下文，不把 schema 当作嵌套内容脱敏机制。

静态生成输入为身份、context、工具/修复计数，输出只有 artifact、finish_reason、token_usage；Vue 首次生成额外输出 context、tool_call_count。验证输入仅接收候选、模型上下文、prompt、结束元数据及跨阶段计数；验证、构建、质量结果与反馈是内部字段。修复分派输入包括修复所需的 validation、build、repair_feedback 和候选，输出不回写质量结论、请求身份和原验证结果。Vue 生成不接收旧 artifact 与验证字段，Vue 修复才接收这些字段。

## Vue 私有历史

initialize 将 context 中 toolResults 移入独立 vue_tool_results 通道，context 在子图运行期间只含稳定上下文和有限重试提示。模型轮次临时组装 toolResults；execute_tool 仅追加历史通道并在成功应用图片时更新 context。validate_turn 只在提示变化时更新 context。finish 将完整历史组装回公共 context，保证后续审查与修复兼容，并清空私有历史与文本副本，避免末尾 checkpoint 重复保存。没有截断、丢弃、外部落库或跨请求记忆。

该调整减少稳定上下文与完整工具历史的共同改写；不承诺物理数据库空间的固定比例收益。入口 checkpoint、父图阶段出口及必要子图仍保存业务所需上下文，旧版本 checkpoint 不保证兼容。

## 验证与边界

新增真实 InMemorySaver 测试，验证所有层级子图的输入过滤与输出隔离、顶层无关字段不进入子图 checkpoint、Vue 模型/批次中间恢复后完整工具历史和 ID 连续、私有历史不泄漏父图、配置与回调传递、递归子图发现。检查 saver 历史中的实际写入通道，确认模型和继续节点不重复写工具历史。沿用三类分支、质量反馈恢复、取消清理与 40 次预算测试。

不改 Java、前端、NDJSON 节点名、预算、模型流式或生产恢复接口；不新增客户端、存储和配置。节点依赖协议目前没有阻碍测试，暂不扩大重构范围。执行 Python 编译、锁文件与全量回归，如实记录 Reranker owner 锁及真实 PostgreSQL 跳过情况，并更新 README/交接文档。
