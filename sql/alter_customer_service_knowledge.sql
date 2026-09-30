-- 客服知识文档及异步 ETL 任务。ID 由应用层 Snowflake 生成。
CREATE TABLE IF NOT EXISTS customer_service_knowledge_document
(
    id              BIGINT                              NOT NULL COMMENT '文档ID' PRIMARY KEY,
    name            VARCHAR(256)                        NOT NULL COMMENT '文档名称',
    fileType        VARCHAR(32)                         NOT NULL COMMENT '文件类型',
    objectKey       VARCHAR(512)                        NOT NULL COMMENT '对象存储键',
    fileSize        BIGINT                              NOT NULL COMMENT '文件大小，字节',
    contentHash     CHAR(64)                            NOT NULL COMMENT '文件内容SHA-256十六进制摘要',
    documentVersion BIGINT     DEFAULT 1                NOT NULL COMMENT '文档内容版本',
    indexedVersion  BIGINT     DEFAULT 0                NOT NULL COMMENT '已建索引的文档版本，0表示未索引',
    etlVersion      BIGINT     DEFAULT 0                NOT NULL COMMENT '当前ETL任务版本，0表示未安排',
    chunkCount      INT        DEFAULT 0                NOT NULL COMMENT '已索引分块数',
    status          VARCHAR(32) DEFAULT 'UPLOADED'      NOT NULL COMMENT '文档处理状态',
    lastErrorCode   VARCHAR(64)                         NULL COMMENT '最近一次处理错误码',
    createdBy       BIGINT                              NOT NULL COMMENT '创建用户ID',
    updatedBy       BIGINT                              NOT NULL COMMENT '更新用户ID',
    createTime      DATETIME   DEFAULT CURRENT_TIMESTAMP NOT NULL COMMENT '创建时间',
    updateTime      DATETIME   DEFAULT CURRENT_TIMESTAMP NOT NULL ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    isDelete        TINYINT    DEFAULT 0                NOT NULL COMMENT '逻辑删除：0-未删除 1-已删除',
    activeContentHash CHAR(64) GENERATED ALWAYS AS (CASE WHEN isDelete = 0 THEN contentHash ELSE NULL END) STORED COMMENT '活动文档去重键',
    UNIQUE KEY uk_knowledge_document_active_hash (activeContentHash),
    KEY idx_knowledge_document_status (status, isDelete, createTime, id),
    KEY idx_knowledge_document_creator (createdBy, isDelete, createTime)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '客服知识文档';

CREATE TABLE IF NOT EXISTS customer_service_knowledge_etl_outbox
(
    id                 BIGINT                              NOT NULL COMMENT '任务ID' PRIMARY KEY,
    documentId         BIGINT                              NOT NULL COMMENT '文档ID',
    documentVersion    BIGINT                              NOT NULL COMMENT '任务对应的文档内容版本',
    etlVersion         BIGINT                              NOT NULL COMMENT '任务版本',
    operation          VARCHAR(32)                         NOT NULL COMMENT 'ETL操作类型',
    status             VARCHAR(32) DEFAULT 'PENDING'       NOT NULL COMMENT '任务状态',
    retryCount         INT         DEFAULT 0               NOT NULL COMMENT '已重试次数',
    nextRetryTime      DATETIME    DEFAULT CURRENT_TIMESTAMP NOT NULL COMMENT '下次可认领时间',
    lastErrorCode      VARCHAR(64)                         NULL COMMENT '最近一次错误码',
    processingOwner    VARCHAR(128)                        NULL COMMENT '当前处理实例标识',
    processingDeadline DATETIME                            NULL COMMENT '当前处理租约截止时间',
    createTime         DATETIME    DEFAULT CURRENT_TIMESTAMP NOT NULL COMMENT '创建时间',
    updateTime         DATETIME    DEFAULT CURRENT_TIMESTAMP NOT NULL ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    UNIQUE KEY uk_document_operation_version (documentId, operation, documentVersion, etlVersion),
    KEY idx_etl_claim (status, nextRetryTime, processingDeadline, id),
    KEY idx_etl_document (documentId, createTime)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '客服知识ETL任务发件箱';

-- 全局 guard 行使 document 与 collection 冲突检查在 MySQL 事务中串行化。
CREATE TABLE IF NOT EXISTS customer_service_knowledge_mutation_guard
(
    id         TINYINT  NOT NULL PRIMARY KEY,
    nextFence  BIGINT   NOT NULL COMMENT '下一个全局单调fence',
    updateTime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '客服知识变更全局守卫';

INSERT IGNORE INTO customer_service_knowledge_mutation_guard(id, nextFence) VALUES (1, 1);

CREATE TABLE IF NOT EXISTS customer_service_knowledge_mutation_lease
(
    operationId VARCHAR(128) NOT NULL PRIMARY KEY COMMENT '不可重用的变更操作ID',
    scope       VARCHAR(256) NOT NULL COMMENT 'document:<id>或collection:<alias>',
    operation   VARCHAR(16)  NOT NULL COMMENT 'INDEX/DELETE/REBUILD',
    fence       BIGINT       NOT NULL COMMENT '全局单调fencing token',
    expiresAt   DATETIME     NOT NULL COMMENT '租约截止时间',
    revokedAt   DATETIME     NULL COMMENT '撤销时间',
    owner       VARCHAR(128) NOT NULL COMMENT '持有实例',
    createTime  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updateTime  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_knowledge_mutation_fence (fence),
    KEY idx_knowledge_mutation_active (scope, revokedAt, expiresAt),
    KEY idx_knowledge_mutation_global (revokedAt, expiresAt)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci COMMENT = '客服知识变更租约审计';
