-- B0b：只建空权威状态，不伪造旧 alias 的发布事实。先停写、排空、备份并统一部署。
CREATE TABLE IF NOT EXISTS customer_service_knowledge_publication_state (
 id TINYINT NOT NULL PRIMARY KEY,
 epoch BIGINT NOT NULL DEFAULT 0 COMMENT '业务可见性版本',
 enabled BOOLEAN NOT NULL DEFAULT FALSE COMMENT '持久协议启用标记，不可配置降级',
 publicationId VARCHAR(128) COLLATE utf8mb4_bin NULL,
 updateTime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
INSERT IGNORE INTO customer_service_knowledge_publication_state(id,epoch,enabled) VALUES(1,0,FALSE);
CREATE TABLE IF NOT EXISTS customer_service_knowledge_publication (
 publicationId VARCHAR(128) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
 epoch BIGINT NOT NULL,
 collectionName VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
 receiptJson MEDIUMTEXT NOT NULL COMMENT '完整不可变候选收据，不含凭据',
 createTime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE KEY uk_knowledge_publication_epoch(epoch),
 UNIQUE KEY uk_knowledge_publication_collection(collectionName)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS customer_service_knowledge_alias_outbox (
 publicationId VARCHAR(128) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
 epoch BIGINT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
 updateTime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
 KEY idx_knowledge_alias_pending(status,epoch)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
