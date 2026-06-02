/*
 * 作者: huang
 * 说明: 用户模块数据库初始化脚本
 * 设计目标:
 * 1. 支持账号+密码注册 / 登录
 * 2. 支持邮箱+密码注册 / 登录
 * 3. 账号和邮箱都可作为唯一登录标识，密码统一存储为哈希值
 */

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

CREATE DATABASE IF NOT EXISTS `yu_ai_code_mother`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `yu_ai_code_mother`;

CREATE TABLE IF NOT EXISTS `sys_user` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '主键ID',
  `account` VARCHAR(32) DEFAULT NULL COMMENT '账号，账号注册时填写，账号登录时使用',
  `email` VARCHAR(128) DEFAULT NULL COMMENT '邮箱，邮箱注册时填写，邮箱登录时使用',
  `password_hash` VARCHAR(255) NOT NULL COMMENT '密码哈希值，不存明文密码',
  `password_salt` VARCHAR(64) DEFAULT NULL COMMENT '密码盐值',
  `nickname` VARCHAR(64) DEFAULT NULL COMMENT '昵称',
  `avatar_url` VARCHAR(512) DEFAULT NULL COMMENT '头像地址',
  `user_profile` VARCHAR(512) DEFAULT NULL COMMENT '用户简介',
  `user_role` VARCHAR(32) NOT NULL DEFAULT 'user' COMMENT '角色身份: user-普通用户 admin-管理员',
  `register_type` TINYINT NOT NULL DEFAULT 1 COMMENT '注册方式: 1-账号注册 2-邮箱注册',
  `status` TINYINT NOT NULL DEFAULT 0 COMMENT '状态: 0-正常 1-禁用',
  `email_verified` TINYINT NOT NULL DEFAULT 0 COMMENT '邮箱是否已验证: 0-否 1-是',
  `last_login_time` DATETIME DEFAULT NULL COMMENT '最后登录时间',
  `last_login_ip` VARCHAR(64) DEFAULT NULL COMMENT '最后登录IP',
  `deleted` TINYINT NOT NULL DEFAULT 0 COMMENT '逻辑删除: 0-未删除 1-已删除',
  `create_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `update_time` DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_sys_user_account` (`account`),
  UNIQUE KEY `uk_sys_user_email` (`email`),
  KEY `idx_sys_user_status` (`status`),
  KEY `idx_sys_user_deleted` (`deleted`),
  CONSTRAINT `chk_sys_user_account_or_email` CHECK (`account` IS NOT NULL OR `email` IS NOT NULL)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_unicode_ci
  COMMENT = '用户表';

/*
 * 使用说明:
 * 1. 账号密码注册: 写入 account + password_hash
 * 2. 邮箱注册: 写入 email + password_hash
 * 3. 账号密码登录: 通过 account + password_hash 校验
 * 4. 邮箱密码登录: 通过 email + password_hash 校验
 * 5. password_hash 建议使用 BCrypt / Argon2 等安全算法生成
 */

SET FOREIGN_KEY_CHECKS = 1;


    -- 应用表
create table app
(
    id           bigint auto_increment comment 'id' primary key,
    appName      varchar(256)                       null comment '应用名称',
    cover        varchar(512)                       null comment '应用封面',
    initPrompt   text                               null comment '应用初始化的 prompt',
    codeGenType  varchar(64)                        null comment '代码生成类型（枚举）',
    deployKey    varchar(64)                        null comment '部署标识',
    deployedTime datetime                           null comment '部署时间',
    priority     int      default 0                 not null comment '优先级',
    userId       bigint                             not null comment '创建用户id',
    editTime     datetime default CURRENT_TIMESTAMP not null comment '编辑时间',
    createTime   datetime default CURRENT_TIMESTAMP not null comment '创建时间',
    updateTime   datetime default CURRENT_TIMESTAMP not null on update CURRENT_TIMESTAMP comment '更新时间',
    isDelete     tinyint  default 0                 not null comment '是否删除',
    UNIQUE KEY uk_deployKey (deployKey), -- 确保部署标识唯一
    INDEX idx_appName (appName),         -- 提升基于应用名称的查询性能
    INDEX idx_userId (userId)            -- 提升基于用户 ID 的查询性能
) comment '应用' collate = utf8mb4_unicode_ci;

-- 对话历史表
create table chat_history
(
    id          bigint auto_increment comment 'id' primary key,
    message     mediumtext                         not null comment '消息',
    messageType varchar(32)                        not null comment 'user/ai',
    appId       bigint                             not null comment '应用id',
    userId      bigint                             not null comment '创建用户id',
    createTime  datetime default CURRENT_TIMESTAMP not null comment '创建时间',
    updateTime  datetime default CURRENT_TIMESTAMP not null on update CURRENT_TIMESTAMP comment '更新时间',
    isDelete    tinyint  default 0                 not null comment '是否删除',
    INDEX idx_appId (appId),                       -- 提升基于应用的查询性能
    INDEX idx_createTime (createTime),             -- 提升基于时间的查询性能
    INDEX idx_appId_createTime (appId, createTime) -- 游标查询核心索引
) comment '对话历史' collate = utf8mb4_unicode_ci;

-- 完整对话历史表(用于加载对话记忆，包含工具调用信息)
create table chat_history_original
(
    id          bigint auto_increment comment 'id' primary key,
    message     mediumtext                         not null comment '消息',
    messageType varchar(32)                        not null comment 'user/ai/toolExecutionRequest/toolExecutionResult',
    appId       bigint                             not null comment '应用id',
    userId      bigint                             not null comment '创建用户id',
    createTime  datetime default CURRENT_TIMESTAMP not null comment '创建时间',
    updateTime  datetime default CURRENT_TIMESTAMP not null on update CURRENT_TIMESTAMP comment '更新时间',
    isDelete    tinyint  default 0                 not null comment '是否删除',
    INDEX idx_appId (appId),                       -- 提升基于应用的查询性能
    INDEX idx_createTime (createTime),             -- 提升基于时间的查询性能
    INDEX idx_appId_createTime (appId, createTime) -- 游标查询核心索引
) comment '对话历史' collate = utf8mb4_unicode_ci;
