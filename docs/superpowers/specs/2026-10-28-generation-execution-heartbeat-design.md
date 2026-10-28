# 生成执行所有者与心跳观测

## 范围

承接已确认的执行所有者、心跳与过期认领方向，本阶段完成可验证的所有者与存活观测基础，不开放恢复认领。首先已执行仲裁表迁移，真实 MySQL/Redis 基线 32 项通过，其中真实资源 10 项。

## 设计

`generation_task` 增加可空 execution_owner、heartbeat_at、execution_expires_at。Spring 任务服务实例以随机 UUID 标识，禁止使用主机名或 IP；新任务创建时绑定 owner 与已有 fence token。只有 INSERT 确认成功才登记进程内待续写任务；插入确认丢失时不主动续写或恢复。历史行字段为空，不回填执行授权。

使用独立 ThreadPoolTaskScheduler，每 15 秒批量遍历本实例在途请求。每条心跳只更新同 requestId/appId/owner/token、RUNNING、尚未过期且仍为当前应用代际的记录；数据库 CURRENT_TIMESTAMP(6) 设置心跳与 90 秒期限，拒绝过期后复活。心跳异常仅脱敏记录，可在期限内重试；条件更新失败停止该条续写，但保留执行登记至真实终态用于原所有者终态 CAS。终态写入同时匹配 owner/token，失败只降级日志，不逆转发布事实；无本实例执行登记的回调不写终态。

任务查询新增 heartbeatAt、executionExpiresAt、heartbeatState，状态由同条 SQL 和数据库时间计算：非 RUNNING 为 TERMINAL，旧行无所有者为 UNKNOWN，代际已改变为 SUPERSEDED，超过期限为 EXPIRED，其余为 LIVE。不公开 execution_owner。取消期间继续观测执行直至真实终态，浏览器断开不停止心跳。终态即使写失败也停止续写，后续自然过期。

## 边界

该期限只判断 Spring 执行控制器的心跳，不证明 Python/模型有进展，也不承担 Redis 锁或静态发布 fencing。EXPIRED 不自动改 FAILED、抢占、取消、续跑、清理 checkpoint 或执行文件操作。Vue 在途副作用仍不具有不可变发布保护。恢复协议需独立串联新 owner、fencing、取消、发布事实和 checkpoint，不能直接根据本阶段状态放行。

## 验证与部署

H2 执行真实 SQL，验证 owner/token 隔离、数据库时钟期限、过期拒绝复活、终态停止、代际更换与历史兼容；服务测试验证登记生命周期、瞬时数据库错误、条件失败及最终清理；真实 MySQL/Redis 在唯一测试 ID 下执行同样期限与所有者条件。增量 SQL 使用 information_schema 条件 DDL 兼容 MySQL 8.0.41，部署前迁移并排空旧执行者后统一升级。Python、NDJSON/SSE 和前端消费协议保持兼容，查询仅新增字段。
