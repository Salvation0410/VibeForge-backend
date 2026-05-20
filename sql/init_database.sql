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
