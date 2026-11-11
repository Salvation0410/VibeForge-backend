# 生成租约所有者隔离实施计划

**Goal:** 确定性复现并阻止同客户端同线程旧租约释放后继应用锁。

**Architecture:** 沿用现有租约、transition lock、MySQL 仲裁与任务服务；仅将应用锁 owner 从工作线程身份变为每次获取的独立逻辑身份。

**Tech Stack:** Java 21、Redisson 3.50.0、JUnit、Mockito、真实本地 MySQL/Redis。

本轮按用户“评估并执行”授权在当前 dev 串行完成；保留两个仓库既有修改，不推送，不重跑已有真实模型提交场景。

- [x] 在 `GenerationTaskResourcesIT` 新增两个时序参数：后继建立后旧 release、旧 unlock 命令延迟到后继建立后。捕获实际 RFuture，显式等待完成，再断言后继锁/状态、第三请求拒绝与新旧提交边界；finally 排空并仅清理自身资源。
- [x] 运行 `mvn test '-Dtest=GenerationTaskResourcesIT#sameClientSuccessorSurvivesLateUnlock'`，显式设置 `AI_TASK_INTEGRATION=true`，保存失败日志到新 ignored 路径；不更改已有测试结论。
- [x] `GenerationLeaseService` 新增静态负向 owner 序列，使用 `lock.tryLockAsync(ownerThreadId).toCompletableFuture().join()`；失败释放沿用该 owner。更新 `GenerationLease` 中文注释。
- [x] `GenerationLeaseServiceTest` 验证每次获取 owner 不同、通过显式 owner 操作锁、异常初始化释放；保留忙时立即拒绝与数据库失败关闭测试。
- [x] `mvn clean -DskipTests compile`，再运行限定租约/任务/仲裁/发布/取消/网关/心跳回归与上述新增真实资源两个时序；所有 Maven 输出保存 ignored 日志，仅报告测试摘要。
- [x] 更新 `doc/generation-task-ledger.md`、真实测试集及交接第 21 节，明确真实资源与自然模型/浏览器的边界。Python 行为未变，无需重跑其全量。
- [x] 两仓库 `git diff --check`、状态、账本 hash 核对；检查提交范围只包含本轮 Java/测试/文档。

## 执行结果

原实现的两个真实资源时序均在后继锁断言失败，日志独立保留。修复后首次定向 15 passed；追加正常 release 的 IDLE 断言后，最终限定回归 147 passed、0 skipped，包含两个新增真实 MySQL/Redis 场景，干净编译通过。Python/前端及其他真实资源场景没有重跑，真实模型新增 0 次，账本 136 且字节 hash 不变。

仅提交本轮文件，使用 `fix(generation): 隔离租约所有者防止迟到释放新锁`，不推送；实际哈希随交付报告。前端客服三文件与后端既有 cancellation-drain-regression 未跟踪计划保留。
