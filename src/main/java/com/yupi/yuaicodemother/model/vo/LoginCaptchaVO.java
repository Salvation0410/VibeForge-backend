package com.yupi.yuaicodemother.model.vo;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/**
 * 登录图形验证码视图
 */
@Data
public class LoginCaptchaVO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    /**
     * Base64 Data URL 格式的验证码图片
     */
    private String captchaImage;

    /**
     * 验证码有效期，单位：秒
     */
    private Integer expireSeconds;
}
