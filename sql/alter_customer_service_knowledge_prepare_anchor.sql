-- INDEX首次准备身份：上线前停写排空并备份，不保存租约proof或下载地址。
CREATE TABLE IF NOT EXISTS customer_service_knowledge_prepare_anchor (
 taskId BIGINT NOT NULL PRIMARY KEY,
 anchorJson MEDIUMTEXT NOT NULL COMMENT '首次context/operationId/fence，不可覆盖',
 createTime DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
