# B0b 权威发布实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development。按任务实施，先规格审查、再质量审查；当前 dev 工作区继续前轮任务，保留用户无关修改。

**Goal:** 实现用户已确认的有界整库不可变候选、MySQL 权威发布及生成前后 epoch 复核。

**Architecture:** Spring 原子提交 publication、活动指针、serving、任务终态和 alias outbox。Python 只写私有候选，读取权威具体集合；alias 为可修复镜像。所有协议以同目录 specs/2026-11-16-rag-authoritative-publication-design.md 为准。

**Tech Stack:** Java 21 / Spring / MyBatis / MySQL；Python / FastAPI / Pydantic / Milvus。

## 任务一：Spring 权威事务与持久协议

文件：新增 publication DTO、Mapper/XML、Service、内部 Controller、alias worker、迁移 SQL；修改 CustomerServiceKnowledgeEtlWorker、CustomerServiceKnowledgeServiceImpl、CustomerServiceAiClient、CustomerServiceProperties、租约 Mapper。

- [x] 为同 ID 重复/冲突、过期 owner、epoch 变化、事务回滚、禁用白名单、bootstrap 条件编写失败测试。
- [x] 新表保存 epoch/head、不可变 receipt、alias outbox。管理变更按 epoch→document 锁序；提交按 epoch→task→lease 锁序。
- [x] worker 在新模式调用 prepare-publication，再本地提交；未知提交结果按稳定 ID 查事实，禁止落入旧 finalizer。
- [x] 添加内部 snapshot/fact 入口、维护 bootstrap 和周期性 head alias 对账；持久 enabled 阻止关闭配置退回旧协议。
- [x] 执行相关 Java 单测和干净编译，审查后仅提交本任务文件。

## 任务二：Python 私有候选与严格收据

文件：新增 orchestration/knowledge_publication.py、协议模型及候选测试；修改 infrastructure/milvus_knowledge.py、必要 ETL 适配。

- [x] 测试准备不触碰源/alias、缺行/坏向量/错模型/超预算、删除最后文档、空重建、精确回读与完成标记。
- [x] 复用已有 INDEX 解析嵌入和 REBUILD 原文校验；复制已验证 f32 向量，随机 _pub_UUID 新集合，64MiB 上限及既有预算。
- [x] 完成回读后写 snapshotHash 完成标记；publication kind 排除旧清理器。
- [x] 执行候选单测及显式隔离 Milvus 验证，审查后提交。

## 任务三：Python 权威读取与 HTTP 集成

文件：新增 infrastructure/spring_knowledge_publication.py、权威 store；修改 api/schemas.py、api/routes.py、app.py、config.py、customer_service_rag.py、README 与 .env.example。

- [ ] 测试整数契约、认证、HTTP 预算/故障关闭、具体 collection 和文档三元组过滤、ContextVar 并发隔离。
- [ ] ReadSnapshot 包含 snapshotHash；检查完成标记/模型/维度。模型前后 assert_current，变化丢弃结果且不自动付费重试；空白名单不调用模型。
- [ ] 新端点严格解析 candidate/reconcile；旧写入口在持久模式启用时拒绝。
- [ ] 逐项运行 targeted tests，再审查与提交。

## 任务四：跨服务验收与维护切换

- [ ] 核对两侧 JSON 字段、数值类型、排序、快照 hash 与错误处理；补充真实隔离 MySQL/Milvus 交错测试。
- [ ] 执行 Java 相关测试及 clean compile；Python compileall、完整 pytest、uv lock --check。必要时停止自有 Python 释放 reranker 锁，完成后离线缓存模式重启。
- [ ] 备份业务库与当前集合身份、排空任务；应用增量迁移，统一新版服务，维护 bootstrap 复制原向量，不调用回答模型。
- [ ] 验证权威 snapshot/具体检索/alias 对账、文档与向量身份、健康和任务状态；失败保留旧集合与证据，不伪造成功。
- [ ] 更新 doc/customer-service-rag-rounds-handoff.md 与迁移/真实验收文档：问题、选择、流程、边界、实际计数/未测项、部署和下一轮。git diff --check 后中文 Conventional Commit。

## 固定验收边界

不得推广 R2/cap2/扩大 TopK；不新增真实回答调用；Python 不直连业务 MySQL。旧集和失败候选不自动 GC。最后 epoch 复核后仍可能变更，不承诺绝对即时撤销。完整进程崩溃/网络丢包未执行则明确保留为未测。README 原有两反引号及用户未跟踪取消计划不纳入提交。
