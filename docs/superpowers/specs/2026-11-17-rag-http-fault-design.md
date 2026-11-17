# 第五轮：跨服务HTTP故障验收

按第四轮交接及用户继续执行授权，增加测试专用Spring Web实例、独立FastAPI进程和真实随机Milvus集合的协作演练。Java仍独占MySQL，使用已有随机schema事务夹具，生产Controller/Service/Mapper负责snapshot/fact和发布事务；测试专用控制接口仅存在test classpath。Python不访问MySQL，使用生产Spring客户端、app factory、权威RAG及alias reconcile。

故障通过回环代理转发后主动断开连接或拒绝snapshot制造，记录实际请求次数；不注入正式环境、不改生产超时/重试策略。固定合成向量、Fake计数模型、Disabled checkpoint和重排避免收费/GPU冲突。覆盖提交响应丢失后原ID确认、Python实例恢复、模型返回后snapshot不可达导致无答案无来源、多实例alias对账。

仅测试隔离namespace；清理精确创建的进程/schema/alias/collection，禁止删除正式版本。不是物理网络随机丢包、事务提交中途崩溃或完整业务Spring配置验收。若代码暴露缺陷，先记录根因再修复并重验。完成相关测试、Java clean compile、Python完整回归、文档和中文提交；不改变生产参数，不自动GC/恢复。
