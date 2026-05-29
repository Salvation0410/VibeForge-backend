package com.yupi.yuaicodemother.model.dto.user;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/**
 * 用户注册表单请求
 */
@Data
public class SysUserRegisterFormRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private String account;

    private String email;

    private String password;

    private String confirmPassword;

    private String nickname;

    private String avatarUrl;

    private String userProfile;

    private String userRole;
}
