-- Task 7 upgrade for databases that already applied alter_customer_service_knowledge.sql.
-- MySQL 8 does not consistently support ADD COLUMN/INDEX IF NOT EXISTS across minor releases,
-- so information_schema checks select one idempotent DDL statement before PREPARE/EXECUTE.
-- Preflight: the query below must return no rows. If it returns duplicates, merge/disable them
-- before continuing. The unique-index DDL also fails closed if duplicates remain.
SELECT contentHash, COUNT(*) AS duplicateCount
FROM customer_service_knowledge_document
WHERE isDelete = 0
GROUP BY contentHash
HAVING COUNT(*) > 1;

SET @cs_schema = DATABASE();
SET @cs_ddl = IF(
    EXISTS(SELECT 1 FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = @cs_schema
             AND TABLE_NAME = 'customer_service_knowledge_document'
             AND COLUMN_NAME = 'activeContentHash'),
    'SELECT 1',
    'ALTER TABLE customer_service_knowledge_document ADD COLUMN activeContentHash CHAR(64) GENERATED ALWAYS AS (CASE WHEN isDelete = 0 THEN contentHash ELSE NULL END) STORED COMMENT ''active document deduplication key'''
);
PREPARE cs_stmt FROM @cs_ddl;
EXECUTE cs_stmt;
DEALLOCATE PREPARE cs_stmt;

SET @cs_ddl = IF(
    EXISTS(SELECT 1 FROM information_schema.STATISTICS
           WHERE TABLE_SCHEMA = @cs_schema
             AND TABLE_NAME = 'customer_service_knowledge_document'
             AND INDEX_NAME = 'uk_knowledge_document_active_hash'),
    'SELECT 1',
    'ALTER TABLE customer_service_knowledge_document ADD UNIQUE INDEX uk_knowledge_document_active_hash (activeContentHash)'
);
PREPARE cs_stmt FROM @cs_ddl;
EXECUTE cs_stmt;
DEALLOCATE PREPARE cs_stmt;

SET @cs_ddl = IF(
    EXISTS(SELECT 1 FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = @cs_schema
             AND TABLE_NAME = 'customer_service_knowledge_document'
             AND COLUMN_NAME = 'lastErrorCode'
             AND CHARACTER_MAXIMUM_LENGTH = 128),
    'SELECT 1',
    'ALTER TABLE customer_service_knowledge_document MODIFY COLUMN lastErrorCode VARCHAR(128) NULL COMMENT ''latest processing error code'''
);
PREPARE cs_stmt FROM @cs_ddl;
EXECUTE cs_stmt;
DEALLOCATE PREPARE cs_stmt;

SET @cs_ddl = IF(
    EXISTS(SELECT 1 FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA = @cs_schema
             AND TABLE_NAME = 'customer_service_knowledge_etl_outbox'
             AND COLUMN_NAME = 'lastErrorCode'
             AND CHARACTER_MAXIMUM_LENGTH = 128),
    'SELECT 1',
    'ALTER TABLE customer_service_knowledge_etl_outbox MODIFY COLUMN lastErrorCode VARCHAR(128) NULL COMMENT ''latest processing error code'''
);
PREPARE cs_stmt FROM @cs_ddl;
EXECUTE cs_stmt;
DEALLOCATE PREPARE cs_stmt;

CREATE TABLE IF NOT EXISTS customer_service_knowledge_mutation_guard
(
    id         TINYINT  NOT NULL PRIMARY KEY,
    nextFence  BIGINT   NOT NULL COMMENT 'next global monotonic fence',
    updateTime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'customer service knowledge mutation guard';

INSERT IGNORE INTO customer_service_knowledge_mutation_guard(id, nextFence) VALUES (1, 1);

CREATE TABLE IF NOT EXISTS customer_service_knowledge_mutation_lease
(
    operationId VARCHAR(128) NOT NULL PRIMARY KEY,
    scope       VARCHAR(256) NOT NULL COMMENT 'document:<id> or collection:<alias>',
    operation   VARCHAR(16)  NOT NULL COMMENT 'INDEX/DELETE/REBUILD',
    fence       BIGINT       NOT NULL,
    expiresAt   DATETIME     NOT NULL,
    revokedAt   DATETIME     NULL,
    owner       VARCHAR(128) NOT NULL,
    createTime  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updateTime  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_knowledge_mutation_fence (fence),
    KEY idx_knowledge_mutation_active (scope, revokedAt, expiresAt),
    KEY idx_knowledge_mutation_global (revokedAt, expiresAt)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = 'customer service knowledge mutation lease audit';
