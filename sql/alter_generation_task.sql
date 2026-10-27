-- 升级 Spring 前执行；任务账本不存提示词、源码和异常正文。
CREATE TABLE IF NOT EXISTS generation_task (
    request_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '服务端请求唯一 ID',
    app_id BIGINT NOT NULL COMMENT '应用 ID',
    user_id BIGINT NOT NULL COMMENT '发起用户 ID',
    code_gen_type VARCHAR(32) NOT NULL COMMENT '生成类型',
    execution_state VARCHAR(16) NOT NULL DEFAULT 'RUNNING' COMMENT '执行状态，不等同发布事实',
    error_code VARCHAR(128) DEFAULT NULL COMMENT '脱敏稳定错误码',
    cancel_requested_at DATETIME(6) DEFAULT NULL COMMENT '取消仲裁获胜时间，不代表取消完成',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6) DEFAULT NULL,
    query_until DATETIME(6) DEFAULT NULL COMMENT '终态可查询 30 天，未结束不自动过期',
    PRIMARY KEY (request_id),
    KEY idx_generation_task_owner (app_id, user_id, created_at),
    KEY idx_generation_task_retention (query_until),
    CONSTRAINT chk_generation_task_state CHECK (execution_state IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='生成任务账本；暂不物理删除唯一性和取消墓碑';
