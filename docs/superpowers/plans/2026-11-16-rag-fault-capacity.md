# 第四轮执行计划

设计：`../specs/2026-11-16-rag-fault-capacity-design.md`。沿用当前dev与既有用户修改；本轮单执行者，避免共享target争用。

1. [x] 增加Java inventory DTO/Mapper SQL/短事务Service/认证Controller；测试分页、坏游标、数字JSON及无正文输出，真实隔离库验证排序/翻页。
2. [x] 增加Python严格分页客户端与手动容量CLI；测试分页变化/故障关闭、分类、未知年龄、序列化预算/iterator关闭、秘密不输出。只允许读取API。
3. [x] 增加子JVM发布故障测试，真实Milvus迟到alias/缺目标/空名单演练；资源均随机隔离且finally核对清理。不是完整双服务网络丢包矩阵。
4. [x] Java相关测试/clean compile，Python compileall/完整pytest（1636通过/24跳过）/lock；首轮2项GPU锁失败后单独及完整复验通过，不改锁策略。
5. [x] 部署只读inventory接口，运行有界真实容量报告，前后比较原head/向量/健康均一致；更新逐轮交接和下一轮说明，中文提交。首次Python就绪前健康连接失败，监听就绪后复验通过。

精确命令随实施补入对应模块README；不扩大原知识预算，不运行自动GC，不新增真实回答/Embedding调用。
