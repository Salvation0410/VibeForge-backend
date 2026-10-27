-- 升级 Spring 前执行；租约代际和发布事实由业务 MySQL 持久化。
SET @generation_fence_column_exists = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE() AND table_name = 'generation_task' AND column_name = 'fence_token'
);
SET @generation_fence_column_ddl = IF(@generation_fence_column_exists = 0,
    'ALTER TABLE generation_task ADD COLUMN fence_token BIGINT NOT NULL DEFAULT 0 COMMENT ''生成租约单调代际''',
    'SELECT 1');
PREPARE generation_fence_column_stmt FROM @generation_fence_column_ddl;
EXECUTE generation_fence_column_stmt;
DEALLOCATE PREPARE generation_fence_column_stmt;

CREATE TABLE IF NOT EXISTS generation_fence (
    app_id BIGINT NOT NULL COMMENT '应用 ID',
    current_token BIGINT NOT NULL DEFAULT 0 COMMENT '当前单调代际',
    PRIMARY KEY (app_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='应用生成 fencing 代际';

CREATE TABLE IF NOT EXISTS generation_publication (
    request_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '已提交发布请求 ID',
    app_id BIGINT NOT NULL COMMENT '应用 ID',
    code_gen_type VARCHAR(32) NOT NULL COMMENT '生成类型',
    fence_token BIGINT NOT NULL COMMENT '提交时的生成代际',
    publication_sequence BIGINT NOT NULL COMMENT '不可变 release 序号',
    hashes_json JSON NOT NULL COMMENT '已提交文件摘要',
    published_at DATETIME(6) NOT NULL COMMENT '发布提交时间',
    PRIMARY KEY (request_id),
    KEY idx_generation_publication_owner (app_id, code_gen_type, published_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='不可变产物发布事实';

CREATE TABLE IF NOT EXISTS generation_active_artifact (
    app_id BIGINT NOT NULL COMMENT '应用 ID',
    code_gen_type VARCHAR(32) NOT NULL COMMENT '生成类型',
    request_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '当前活动发布请求 ID',
    fence_token BIGINT NOT NULL COMMENT '活动发布代际，0 表示升级前基线',
    updated_at DATETIME(6) NOT NULL COMMENT '指针更新时间',
    PRIMARY KEY (app_id, code_gen_type),
    KEY idx_generation_active_request (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='每应用每生成类型的权威活动版本指针';
