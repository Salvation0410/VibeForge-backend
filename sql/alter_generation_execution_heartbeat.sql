-- 所有 Spring 实例升级前执行；旧行不回填执行所有者或心跳。
SET @generation_owner_ddl = IF((SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'generation_task' AND column_name = 'execution_owner') = 0,
    'ALTER TABLE generation_task ADD COLUMN execution_owner VARCHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL COMMENT ''执行所有者随机标识''', 'SELECT 1');
PREPARE generation_owner_stmt FROM @generation_owner_ddl;
EXECUTE generation_owner_stmt;
DEALLOCATE PREPARE generation_owner_stmt;

SET @generation_heartbeat_ddl = IF((SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'generation_task' AND column_name = 'heartbeat_at') = 0,
    'ALTER TABLE generation_task ADD COLUMN heartbeat_at DATETIME(6) NULL COMMENT ''数据库时间的最后心跳''', 'SELECT 1');
PREPARE generation_heartbeat_stmt FROM @generation_heartbeat_ddl;
EXECUTE generation_heartbeat_stmt;
DEALLOCATE PREPARE generation_heartbeat_stmt;

SET @generation_expiry_ddl = IF((SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'generation_task' AND column_name = 'execution_expires_at') = 0,
    'ALTER TABLE generation_task ADD COLUMN execution_expires_at DATETIME(6) NULL COMMENT ''心跳观测期限，不授权恢复''', 'SELECT 1');
PREPARE generation_expiry_stmt FROM @generation_expiry_ddl;
EXECUTE generation_expiry_stmt;
DEALLOCATE PREPARE generation_expiry_stmt;
