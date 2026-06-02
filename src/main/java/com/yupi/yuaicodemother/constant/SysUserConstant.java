package com.yupi.yuaicodemother.constant;

/**
 * 用户相关常量
 */
public interface SysUserConstant extends UserConstant {

    /**
     * 邮箱注册验证码
     */
    String EMAIL_REGISTER_CODE = "email_register_code";

    /**
     * 邮箱注册验证码对应邮箱
     */
    String EMAIL_REGISTER_CODE_EMAIL = "email_register_code_email";

    /**
     * 邮箱注册验证码过期时间
     */
    String EMAIL_REGISTER_CODE_EXPIRE_TIME = "email_register_code_expire_time";

    /**
     * 邮箱注册验证码最后发送时间
     */
    String EMAIL_REGISTER_CODE_SEND_TIME = "email_register_code_send_time";

    /**
     * 登录图形验证码
     */
    String LOGIN_CAPTCHA_CODE = "login_captcha_code";

    /**
     * 登录图形验证码过期时间
     */
    String LOGIN_CAPTCHA_EXPIRE_TIME = "login_captcha_expire_time";
}
