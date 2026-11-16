# 客服知识权威发布迁移与恢复边界

本文件记录 B0b 的维护切换顺序。实施与验收结果以 `customer-service-rag-rounds-handoff.md` 的第三轮记录为准；在该记录确认部署前，下列命令不是已执行结果。用户已确认整库不可变候选与生成前后 epoch 复核。

2026-11-16本机已完成下述维护流程，epoch=1、11 ACTIVE/1 DISABLED、72块/11 manifest原字段及f32向量一致，旧集合保留。业务表备份路径、哈希、publication身份及未测项见逐轮交接。关闭维护并重启后bootstrap请求被拒绝，两端健康及具体检索复验通过；这只代表本机切换验收，不代表完整多实例故障矩阵通过。

## 切换前提

- 所有 Spring/Python 实例使用同一新版协议，禁止旧二进制混跑。MySQL 仍是业务数据唯一所有者，Python 仅通过带认证的 Spring 内部接口取得权威快照。
- 排空知识任务、停止管理写入和 worker；确认没有 PENDING/PROCESSING。关闭旧实例，保留所有原文 registry、旧集合、向量、墓碑和 alias 信息。
- 保存本次 SQL 备份、SHA256、文档 serving 三元组及当前具体 collection。先在隔离 MySQL/Milvus 运行边界测试，再运行必要 Java/Python 回归。
- 切换不提高原有文档、块、文本和向量预算。整库候选还受 64 MiB 序列化预算限制；超限中止，不能自行放宽预算。
- Spring AI 响应默认仍受 `max-response-bytes=65536` 限制，收据会额外限制可发布规模；文档数小于1000不代表收据一定能通过。Python准备阶段需先预检收据预算，不能自动扩大HTTP上限。

## 维护流程

1. 停写并备份业务知识表；应用 `sql/alter_customer_service_knowledge_publication.sql`。此脚本只建立发布状态、不可变事实和 alias 对账事件，不伪造旧发布事实。
2. Spring 设置 `ai.customer-service.publication-enabled=true` 和 `ai.customer-service.publication-bootstrap-allowed=true`；Python 设置 `AI_SERVICE_RAG_PUBLICATION_ENABLED=true`。原有认证、模型和集合 alias 配置保持一致。启动新版服务，未建立 head 前实际 RAG 读取拒绝服务。
3. 具有管理员权限的维护请求调用 `POST /api/admin/customer-service/knowledge/publications/bootstrap`，请求体为 `{"sourceCollection":"已核对的旧具体collection"}`。不传 alias。服务使用真实 registry 原文下载与解析，复核旧块、定位、哈希、模型和维度，并复制原 f32 向量到新私有候选，不调用 Embedding。
4. 核对返回的 publication、epoch、candidateCollection、snapshotHash；再通过 `GET /api/internal/customer-service/knowledge-publications/snapshot` 读取持久事实与白名单。维护候选和旧集合必须不同，72 块现有语料须逐项核对，不只比较条数。
5. alias 对账可能晚于数据库提交。实际读取使用权威具体 collection，不能以 alias 尚未更新认定发布失败。对账响应必须复核当前 head，周期任务即使事件已 ACK 也继续校正迟到覆盖。
6. 关闭 `publication-bootstrap-allowed` 并重启相应实例，恢复知识管理写入。保持 publication-enabled；禁止将关闭开关当作回滚方案。

本机后续普通启动需保留私有配置：Spring `ai.customer-service.publication-enabled=true`、`publication-bootstrap-allowed=false`，Python `.env` 的 `AI_SERVICE_RAG_PUBLICATION_ENABLED=true`。开关的仓库默认值仍为false；若运行环境丢失该配置，持久模式检查会拒绝旧协议，不能通过切alias绕过。私有配置和原文件备份均不提交Git。

所有内部接口使用原内部 Bearer；不要在命令历史、文档或提交里写真实 token、cookie、下载 URL 或数据库认证信息。

## 故障判断

| 场景 | 处理与边界 |
| --- | --- |
| Python 构建中断、预算超限或精确回读不符 | 不返回可提交收据，不改源、head 或 alias；失败候选保留，尚无自动 GC。 |
| 构建期间禁用、启用、替换或另一次发布 | Spring epoch 改变，旧候选提交失败；禁用立即移除读取白名单。重试须以新 head 重新准备。 |
| claim 被接管或 lease 失效 | 旧执行无权提交。检查后暂停仍可留下私有候选，但不能据此激活。 |
| 数据库提交确认丢失 | 按稳定 publicationId 查询事实，相同收据返回原事实；查询仍失败则保持未知，不能标记“未执行”或换 ID 绕过。 |
| alias 写失败或被旧执行迟到覆盖 | 权威读取不受镜像选择影响；周期对账校正，发布事实不撤销。alias 无 CAS，不能保证瞬间始终匹配。 |
| 权威数据库/API 不可用 | 读写失败关闭，不回退旧 alias。 |
| publication 集合缺失、完成标记坏、模型/维度不符 | 失败关闭，保留证据；不自动换到别的集合或重算原向量。 |
| 回答模型运行期间 epoch 改变 | 丢弃在途回答及引用，不自动进行付费重试；最后复核后仍可能再次改变，不承诺绝对即时撤销。 |
| 最后一篇文档 DELETE | 可发布完整的空集合；空 REBUILD 不能被当作清空命令。 |
| 配置关闭的存活新版进程 | 每次执行前仍核对 durable mode；已经启用时拒绝旧路径。旧存储写路径另外拒绝 publication 具体 target，防止检查后暂停恢复污染。 |

## 恢复与回滚

B0b 不是跨 MySQL/Milvus 的分布式事务，也不是向量丢失后的灾备。其保证依赖完整私有候选、持久发布指针及权威读取共同成立。不要手工删除发布状态、活动 publication、旧集合或原文 registry；不要切 alias 或直接回退旧版本程序。

需要恢复时先停写排空并保存事实。若当前集合仍完整，沿权威事实恢复新版服务；若集合丢失，需要另行设计和验收从原文/保存向量恢复的流程，不能伪造相同 snapshotHash。模型升级、分块策略变化走新 INDEX 代际和独立迁移。旧 publication 和失败候选的回收必须另行引入引用保护、隔离期和在途读取约束。
