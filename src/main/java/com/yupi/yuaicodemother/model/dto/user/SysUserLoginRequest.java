package com.yupi.yuaicodemother.model.dto.user;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/**
 * 用户登录请求参数
 *
 * 支持：
 * 1. 账号 + 密码登录
 * 2. 邮箱 + 密码登录
 */
@Data
public class SysUserLoginRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    /**
     * 账号，账号登录时使用
     */
    private String account;

    /**
     * 邮箱，邮箱登录时使用
     */
    private String email;

    /**
     * 登录密码
     */
    private String password;

    /**
     * 登录图形验证码
     */
    private String captchaCode;
}
