package com.yupi.yuaicodemother.model.dto.user;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/**
 * 发送邮箱注册验证码请求
 */
@Data
public class UserEmailCodeSendRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    /**
     * 注册邮箱
     */
    private String email;
}
