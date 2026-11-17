# 第七轮：可核验INDEX准备结果恢复

用户已确认可核验准备结果恢复；本设计具体化第六轮交接的推荐方案。Spring持久化每任务首次INDEX attempt的context/operationId/fence，不保存proof、下载地址或向量。登记短事务沿epoch→task→lease锁序检查当前上下文；同任务首次记录不可覆盖，上下文变化明确拒绝恢复。Python不连接MySQL。

prepare请求可携带prepareAnchor（首次context/operationId/fence）。候选名由alias和context/operationId/fence的规范hash确定，避免全库发现扫描。首次发送按该身份构建；重试先查旧候选完成标记及完整快照、预算、模型/维度/文档身份。旧候选缺失、部分完成、损坏或身份漂移失败关闭，不降级重新Embedding。重新下载并按当前切分配置解析目标原文，逐项比较分块/定位/向量规范行，再复制旧向量生成当前新租约收据及新候选。旧候选不直接激活，commit继续核对当前epoch/claim/lease/业务身份。

同一当前attempt的重复请求仅在完成候选完整验收和许可复核后返回同收据；已有未完成集合不能覆盖。并发首次请求可能名称冲突而失败，不增加锁或任意重试；下次请求可验收已完成结果。旧anchor新lease必须同scope/INDEX、同context且fence递增；anchor不是授权，正式HTTP前后复核权威上下文，准备写入按新lease许可。

无anchor旧客户端继续旧行为；恢复只覆盖INDEX，不实现DELETE/REBUILD缓存、任务自动接管、GC或计费exactly-once。首次计算完成前退出仍可能已经付费但无可恢复候选，需新INDEX任务重算；不能以恢复缺失证明上次未收费。新迁移表缺失时先于HTTP发送失败。上线先排空/备份、加表、Python与Spring一致切换，保留旧事实和候选。

验收包括响应200丢失后重试Embedding总1次、新候选独立/事实唯一；旧lease撤销、当前上下文/模型变化、缺失/半写/损坏、重复当前请求和Python重启。资源测试随机schema/alias，供应商替身；生产只读原字段/f32一致核对，不重建正式库。
