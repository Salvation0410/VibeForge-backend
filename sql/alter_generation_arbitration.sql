-- 升级全部 Spring 实例前执行；历史请求的取消与提交资格不自动回填或授权恢复。
CREATE TABLE IF NOT EXISTS generation_arbitration (
    request_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '请求 ID',
    app_id BIGINT NOT NULL COMMENT '应用 ID',
    fence_token BIGINT NOT NULL COMMENT '签发代际',
    decision VARCHAR(16) NOT NULL DEFAULT 'ACTIVE' COMMENT 'ACTIVE/CANCELLED/COMMITTING',
    cancel_requested_at DATETIME(6) NULL COMMENT '首次获胜取消时间',
    PRIMARY KEY (request_id),
    UNIQUE KEY uk_generation_arbitration_token (app_id, fence_token),
    CONSTRAINT chk_generation_arbitration_decision CHECK (decision IN ('ACTIVE', 'CANCELLED', 'COMMITTING'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='生成取消与静态提交资格仲裁';
