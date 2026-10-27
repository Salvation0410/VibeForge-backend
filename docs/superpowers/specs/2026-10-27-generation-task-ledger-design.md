# Spring 生成任务账本与查询边界

## 范围与取舍

采用独立 MySQL 任务表。复用 Redis 状态无法保留终态；复用 Python 节点摘要则破坏业务归属边界。新增任务记录服务和只读查询，在已授权的下一阶段范围内实施。生产恢复、租约重获、自动重试与 SSE 重放留待后续。

## 数据与生命周期

服务端 UUID 请求 ID 全局唯一，记录 appId、userId、codeGenType、执行状态 RUNNING/SUCCEEDED/FAILED/CANCELLED、稳定错误码、取消请求时间及创建/更新/完成/查询截止时间。不存提示词、源码、异常正文、令牌；无业务审核字段，因为任务不属于社区内容。不使用逻辑删除；终态查询保留 30 天，RUNNING 不自动过期，暂不物理删除任务唯一性及取消墓碑。

获取应用租约后、历史写入和模型调用前插入任务；插入失败释放租约并拒绝执行。终态和取消观测使用条件更新，终态不可被迟到回调覆盖；重复请求 ID 不触发模型调用。同一 Flux 重订阅不重用旧请求 ID。终态记录失败只记日志，不把已经发布的成功翻转为失败；缺失终态保持 RUNNING，由查询的独立租约/发布证据暴露不确定性。

取消仍由现有 Redis 提交状态门仲裁，获胜后 best-effort 记取消墓碑再通知下游；取消请求时间不是取消完成证明。MySQL、Redis、文件系统没有跨资源事务，本阶段账本不得作为恢复授权来源。

## 查询与证据

新增 `/api/apps/chat/gen/code/task`，appId 必填、requestId 可选（省略查询当前用户最近未过期任务）。检查登录、现存应用所有权及记录 userId/appId，跨应用、跨用户请求不得查询。既有活动 status 和 SSE 不变。

响应分别提供 executionState、leaseState 和 publicationState。实时租约只在 requestId 匹配时展示，否则为 UNKNOWN；不把无租约解释为任务已失败。静态发布按 VersionedArtifactStore 的提交标记与墓碑核对，有保留 release 时复核 manifest 和文件摘要；仅有墓碑不能证明提交。读证据不修复指针或写入任务，不触发生成。Vue 返回 NOT_VERSIONED；文件读取异常返回 UNAVAILABLE。各证据不是跨资源原子快照，执行失败与发布已提交可以同时存在。

## 部署、回滚与验证

升级 Spring 前执行新增幂等建表 SQL；不自动运行数据库迁移。回滚应用保留表。新增表不会改变已有应用和 Python checkpoint。测试覆盖 SQL 唯一性/条件更新/查询截止、生命周期失败关闭、取消与迟到终态、权限、发布证据及三种类型；Java 干净编译、相关测试和 diff 检查通过后中文提交。真实 MySQL/Redis/模型和端到端结果独立说明。
