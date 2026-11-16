-- B0a v2 版本基础迁移。先停知识 worker 和写入口、排空旧协议任务并备份。
-- 本脚本针对尚无 v2 列的旧库执行一次；MySQL DDL 非事务，不可与在线旧实例混跑。
-- 先列出不能凭当前对象恢复的旧 serving；禁止把当前 objectKey 冒充历史原文。
SELECT id, documentVersion, indexedVersion, etlVersion, status
FROM customer_service_knowledge_document
WHERE isDelete=0 AND status <> 'DISABLED' AND indexedVersion>0
  AND (indexedVersion<>documentVersion OR status<>'ACTIVE' OR etlVersion<=0);

DROP PROCEDURE IF EXISTS assert_knowledge_v2_migration_ready;
DELIMITER $$
CREATE PROCEDURE assert_knowledge_v2_migration_ready()
BEGIN
    IF EXISTS (SELECT 1 FROM customer_service_knowledge_etl_outbox WHERE status IN ('PENDING','PROCESSING')) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Drain legacy PENDING/PROCESSING knowledge tasks before v2 migration';
    END IF;
    -- 旧 schema 不记 serving ETL；仅当前 ACTIVE 可确定身份。先恢复原文登记或完成旧版重索引。
    IF EXISTS (SELECT 1 FROM customer_service_knowledge_document
               WHERE isDelete=0 AND status<>'DISABLED' AND indexedVersion>0
                 AND (indexedVersion<>documentVersion OR status<>'ACTIVE' OR etlVersion<=0)) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='Legacy serving revision unavailable; reconcile original content/index identity first';
    END IF;
END$$
DELIMITER ;
CALL assert_knowledge_v2_migration_ready();
DROP PROCEDURE assert_knowledge_v2_migration_ready;

ALTER TABLE customer_service_knowledge_document
    ADD COLUMN activationRevision BIGINT NOT NULL DEFAULT 1 COMMENT '当前激活修订',
    ADD COLUMN indexedEtlVersion BIGINT NOT NULL DEFAULT 0 COMMENT 'serving索引修订',
    ADD COLUMN indexedActivationRevision BIGINT NOT NULL DEFAULT 0 COMMENT 'serving激活修订';

-- 内容版本原文登记：不删除旧 serving 所引用的 OSS 对象。
CREATE TABLE IF NOT EXISTS customer_service_knowledge_content_revision
(
    documentId BIGINT NOT NULL COMMENT '文档ID',
    documentVersion BIGINT NOT NULL COMMENT '不可变内容修订',
    name VARCHAR(256) NOT NULL,
    fileType VARCHAR(32) NOT NULL,
    objectKey VARCHAR(512) NOT NULL,
    fileSize BIGINT NOT NULL,
    contentHash CHAR(64) NOT NULL,
    createTime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (documentId, documentVersion)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='客服知识不可变原文登记';

-- 只登记数据库确实持有的当前原文，绝不伪造更早内容版本的对象键。
INSERT INTO customer_service_knowledge_content_revision
(documentId,documentVersion,name,fileType,objectKey,fileSize,contentHash,createTime)
SELECT id,documentVersion,name,fileType,objectKey,fileSize,contentHash,createTime
FROM customer_service_knowledge_document;

UPDATE customer_service_knowledge_document
SET indexedEtlVersion=etlVersion,indexedActivationRevision=activationRevision
WHERE isDelete=0 AND status='ACTIVE' AND indexedVersion=documentVersion AND indexedVersion>0;
-- DISABLED/已删除记录不恢复 serving；重新启用时新激活与索引修订由新版业务事务生成。
