# 第十轮：准备仲裁故障验收与管理员只读观测

用户授权继续交接中的下一轮。本轮优先证实第九轮持久资格在退出、丢确认和撤销下的行为，不改计算或恢复策略。

## 推荐设计及取舍

新增管理员单任务 GET `/api/admin/customer-service/knowledge/{id}/tasks/{taskId}/prepare-execution`，Controller → 独立只读 Service → Mapper；管理员鉴权复用 AuthCheck。以文档与任务联合核对，任务不存在或归属不符返回 NOT_FOUND。只返回 taskId、记录状态、观察状态、固定原因、观察时间与观察期限。未登记返回 NOT_REGISTERED；STARTED 未到期限为 BUSY，到期为 UNKNOWN/OBSERVATION_EXPIRED；显式 UNKNOWN 为 UNKNOWN/EXECUTION_UNCONFIRMED；COMPLETE 为 COMPLETE/COMPLETION_RECORDED；未知持久状态为 UNKNOWN/INVALID_EXECUTION_STATE。COMPLETE 只代表收据登记，不代表发布、向量核验或当前活动版本。时间字段明确 UTC；数据库 createTime/updateTime 是数据库本地时间，不能直接标为 UTC，本轮只使用已定义 UTC 的观察期限与服务器观察时间，不扩张表结构。

推荐单任务接口查询量固定，不获取 receiptJson、executorId、operationId、proof、原文或 URL，不加 FOR UPDATE、不更新超时状态、不调用 Python/Milvus/Embedding。观测只解释执行记录，不判断当前 lease/claim/epoch 或恢复可行性；不构成一致发布快照。控制面失败返回错误，不能伪装未登记。无记录可能是旧任务、无 anchor、尚未申请等，不能推断未计费。

备选一：全库状态分页列表，适合巡检，但增加索引、扫描预算与跨页一致性设计。备选二：只补验收并沿用数据库人工查状态，生产改动最少，但运维容易误解 UNKNOWN 和收据字段。建议本轮单任务只读接口，后续根据使用量决定巡检列表。接口待用户确认后实施；故障测试先完成。

## 故障验证

1. 子 JVM 在 BEGIN 已提交、确认前 halt：父连接先验证持久 STARTED，再同身份重发必须 BUSY，过期后 UNKNOWN，始终不重授。
2. 子 JVM 在 COMPLETE 已提交、确认前 halt：父连接核对持久 COMPLETE 与相同收据，重传完成幂等，BEGIN 不授资格，仍无发布事实。
3. 独立 Python Preparer 在授权后/Embedding 后退出或取消：重试未完整候选不重算；完整候选完成登记响应丢失后重新对象回读恢复零新增调用。以替身计数报告，不称真实计费证明。
4. 原执行长期暂停后恢复：观察期限只改变观察，无自动接管；当前许可仍有效时原执行者可以完成；epoch/lease 撤销则拒绝登记/发布。
5. 控制面不可用：BEGIN 不重发、不付费，COMPLETE 丢确认保持原异常；不额外发送 UNKNOWN。

子 JVM 与离线 Python 场景分别报告，不合称完整 HTTP 网络分区/事务中途强杀。随机 schema 使用现有隔离夹具精确清理；正式库仅只读核对，不 INDEX/REBUILD/GC。

## 交付

运行目标 Java/Python 与必要资源场景，记录计数和未测项，更新逐轮交接与对应 README/AGENTS。保留用户既有 README 格式改动与取消计划。必要验证后仅提交本轮文件，中文 Conventional Commit。下一轮再判断跨服务故障矩阵剩余缺口、运维人工恢复与 GC 引用保护；质量 cap2/R2 单独评估。
