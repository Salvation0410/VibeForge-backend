-- 修复旧版本用本地时间写入、但工作线程使用 UTC 扫描的首次待处理任务。
-- 在业务数据库执行；不改变表结构，不重置失败任务或已经安排的重试。
-- 先确认受影响记录：
SELECT id, documentId, operation, status, retryCount, nextRetryTime,
       UTC_TIMESTAMP() AS currentUtcTime
FROM customer_service_knowledge_etl_outbox
WHERE status = 'PENDING' AND retryCount = 0 AND lastErrorCode IS NULL
  AND nextRetryTime > UTC_TIMESTAMP();

-- 首次任务应立即执行；可重复执行，已领取的任务不受影响。
UPDATE customer_service_knowledge_etl_outbox
SET nextRetryTime = UTC_TIMESTAMP()
WHERE status = 'PENDING' AND retryCount = 0 AND lastErrorCode IS NULL
  AND nextRetryTime > UTC_TIMESTAMP();
