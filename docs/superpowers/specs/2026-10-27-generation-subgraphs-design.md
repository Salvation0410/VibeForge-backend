# 生成工作流子图设计

本设计对应会话中用户批准实施的第一版方案。

## 结构

主图保留 input_guard、context_prepare、类型路由、artifact_publish 和 finalize。
三个生成节点分别调用 HTML、MULTI_FILE、Vue 编译子图；生成子图以原有模型节点为入口。
生成后进入统一 verification_repair 子图，封装硬校验、Vue 构建、质量审查和有限修复。
失败以异常退出，由请求执行器生成失败事件；通过后由主图决定静态产物发布或 Vue 完成。

## 状态与运行依赖

父子图复用 WorkflowState，子图以 output_schema 限定可回传字段；请求身份不由子图改写。
repair_count 和 tool_call_count 跨阶段累计。运行时 emitter、工具网关、取消注册表和发布终态字典不进入 state。
原有生成类型协议、工具调用 ID、有限重试和图片检查保持一致。

## 持久化与事件

主图注入已有 saver，子图默认继承；不启用跨调用记忆，不增加数据库配置。
请求终态调用 adelete_thread 清理父子命名空间；保留既有 TTL 兜底。
事件继续由同一 EventEmitter 生成，保留原节点名称；内部 namespace 不直接暴露给 Java。
发布后的权威成功记录继续位于请求级终态字典，避免检查点错误反转已发布结果。
Vue 快照及 Reviewer 原文仅在质量审查调用内存中使用。

## 本次边界

先提取节点与工具循环，再替换图组装；不改 Spring、前端、模型流式调用或公开接口。
Vue 循环仍是单节点，不承诺逐文件恢复。生产自动恢复和人工中断接口需要独立业务设计。

## 验证

沿用完整离线测试，新增真实 InMemorySaver 的子图命名空间、输出字段隔离、恢复不重复 Reviewer、成功/失败/取消清理和执行中事件可见性测试。
真实 PostgreSQL、真实模型、Redis 工具幂等及端到端 SSE 不以离线测试替代。
