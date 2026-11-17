# 第七轮INDEX准备恢复执行计划

**目标：** 恢复完成但丢响应的INDEX结果，重试不重复Embedding。
**架构：** Spring不可变首次anchor，Python确定性私有候选与全量核验、新lease复制发布。

- [x] 协议及首次anchor登记完成，发送前持久化，旧路径兼容。
- [x] Python确定性名称、完整验收、同attempt复用、新lease向量复制及HTTP前后权威核对完成。
- [x] 新增9项Python边界及MySQL/worker测试，HTTP重启恢复合计Embedding1次。
- [x] Java干净编译、目标及隔离资源通过；Python1645通过/24跳过，compileall/lock通过。
- [x] 正式停写排空、备份8表、加空anchor表、两端重启和原字段/f32一致核对；文档和中文提交完成（eb8386a）。
