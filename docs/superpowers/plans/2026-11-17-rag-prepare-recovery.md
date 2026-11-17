# 第七轮INDEX准备恢复执行计划

**目标：** 恢复完成但丢响应的INDEX结果，重试不重复Embedding。
**架构：** Spring不可变首次anchor，Python确定性私有候选与全量核验、新lease复制发布。

- [ ] 协议新增PrepareAnchor/context-operationId-fence及可选请求字段；新增SQL表及Mapper首记录方法，Service沿原锁序核对当前context，worker发送前登记；旧路径兼容。
- [ ] Python实现确定性名称、anchor形状/上下文校验、已完成结果读取与同attempt复用；恢复ETL使用逐文本匹配原向量，最终规范行严格比较，HTTP前后权威核对。
- [ ] 单元测试缺失/半写/损坏/模型变化/定位变化/重复当前请求；MySQL验证首次anchor不可覆盖与epoch冲突；改隔离HTTP runner断响应后重启Python并检验Embedding合计1次。
- [ ] Java干净编译及相关/隔离资源tests；Pythoncompileall/完整pytest/lock；停止管理的Python释放GPU锁后回归，再恢复服务。
- [ ] 文档同步协议、迁移、故障处理和未测边界，逐轮交接；仅提交本轮改动并记录中文提交hash。正式加表前核查未完成任务和备份，切换后只读验证原知识库不变。
