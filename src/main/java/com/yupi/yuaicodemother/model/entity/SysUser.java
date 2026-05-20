package com.yupi.yuaicodemother.model.entity;

import com.mybatisflex.annotation.Id;
import com.mybatisflex.annotation.KeyType;
import com.mybatisflex.annotation.Table;
import com.mybatisflex.core.keygen.KeyGenerators;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serial;
import java.io.Serializable;
import java.time.LocalDateTime;

/**
 * 用户表实体类。
 *
 * @author song
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@Table("sys_user")
public class SysUser implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    /**
     * 主键ID
     */
    @Id(keyType = KeyType.Generator, value = KeyGenerators.snowFlakeId)
    private Long id;

    /**
     * 账号，账号注册时填写，账号登录时使用
     */
    private String account;

    /**
     * 邮箱，邮箱注册时填写，邮箱登录时使用
     */
    private String email;

    /**
     * 密码哈希值，不存明文密码
     */
    private String passwordHash;

    /**
     * 密码盐值
     */
    private String passwordSalt;

    /**
     * 昵称
     */
    private String nickname;

    /**
     * 头像地址
     */
    private String avatarUrl;

    /**
     * 用户简介
     */
    private String userProfile;

    /**
     * 角色身份，可取值 user/admin
     */
    private String userRole;

    /**
     * 注册方式: 1-账号注册 2-邮箱注册
     */
    private Integer registerType;

    /**
     * 状态: 0-正常 1-禁用
     */
    private Integer status;

    /**
     * 邮箱是否已验证: 0-否 1-是
     */
    private Integer emailVerified;

    /**
     * 最后登录时间
     */
    private LocalDateTime lastLoginTime;

    /**
     * 最后登录IP
     */
    private String lastLoginIp;

    /**
     * 逻辑删除: 0-未删除 1-已删除
     */
    private Integer deleted;

    /**
     * 创建时间
     */
    private LocalDateTime createTime;

    /**
     * 更新时间
     */
    private LocalDateTime updateTime;
}
