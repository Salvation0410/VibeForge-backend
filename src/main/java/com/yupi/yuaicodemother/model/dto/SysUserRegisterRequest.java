package com.yupi.yuaicodemother.model.dto;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/**
 * 用户注册请求参数
 *
 * 支持：
 * 1. 账号 + 密码注册
 * 2. 邮箱 + 密码注册
 */
@Data
public class SysUserRegisterRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    /**
     * 账号，账号注册时必填；邮箱注册时可选
     */
    private String account;

    /**
     * 邮箱，邮箱注册时必填；账号注册时可选
     */
    private String email;

    /**
     * 密码明文，后端入库前应加密为 passwordHash
     */
    private String password;

    /**
     * 确认密码，用于前端二次校验
     */
    private String confirmPassword;

    /**
     * 昵称，可选
     */
    private String nickname;

    /**
     * 用户简介，可选
     */
    private String userProfile;

    /**
     * 角色身份，可选，默认普通用户
     */
    private String userRole;
}
