# 第四轮：发布故障证据与候选容量观测

用户已要求按逐轮交接继续执行。本设计落实第三轮末尾已列明的隔离故障演练、只读容量观测；不引入自动恢复、清理、付费模型或质量参数推广。

## 范围和选择

采用有界、显式opt-in的资源演练与手动只读报告。仅增加进程内Mock不能证明进程退出后的持久事实；直接演练业务库或删除旧候选会影响当前索引。本轮使用随机MySQL schema和Milvus alias，进程级发布测试使用真实生产Service/Mapper/事务，向量演练使用真实Milvus与注入式模型，严格分别记录覆盖层次。整套双Spring Boot/Python供应商生产配置不作为默认测试依赖。

## 只读报告

Spring新增带现有Bearer的内部inventory分页：按不可变fact epoch升序，每页最多100条，额外查1条判断hasMore，记录publicationId、epoch、collectionName、createTime及当前enabled/epoch/head。每页持state锁，Python翻页核对同一控制身份；页面变化、超页数、坏类型或重复身份则整体失败。不得输出receipt中的文档名、原文、下载URL或认证信息。生产MySQL仍只由Java访问。

Python提供手动CLI，限制最多1000条发布事实和1000个候选。只观察当前alias命名空间、严格publication kind与aliasHash；当前引用、历史事实引用、无已观察事实、元数据坏、目标缺失分别呈现。无已观察事实不等于没有在途/未知提交引用。报告没有删除建议字段，也不执行任何Milvus写操作。

默认仅观察row_count与Milvus创建时间。显式measure-json时有界迭代全部行，只累加规范JSON字节数；每集合64MiB、总计256MiB，超过预算保留未知标记，不输出正文或向量。该指标是序列化JSON大小，不是磁盘/索引/副本占用。时间未知、未来或非法时年龄为null。读前后复核控制状态及候选集合清单，变化报告不完整，不自动重试。

## 故障证据

增加真实子JVM在提交前退出、提交后确认发出前退出的演练：持久fact/head/serving/task逐项检查，原ID重试只返回原事实，冲突不覆盖。另以真实隔离Milvus覆盖迟到alias恢复/对账收敛、目标缺失、空名单；收费模型与query Embedding用可计数Fake。不同连接/进程、HTTP故障替身、真实资源和完整应用实例分别标注，不将局部证据宣传为全部故障矩阵完成。

## 验收和交接

普通测试不访问资源；真实测试显式opt-in并精确清理自身命名空间。执行Java相关测试/干净编译和Python完整回归/compileall/lock；本机容量观测只读运行，核对线上epoch/head和向量未变。更新README、AGENTS和逐轮交接，报告失败与未测项、提交与部署状态及后续故障矩阵缺口。
