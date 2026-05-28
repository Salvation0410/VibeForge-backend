package com.yupi.yuaicodemother.model.dto;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/**
 * 用户更新请求
 */
@Data
public class SysUserUpdateRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private String account;

    private String email;

    private String nickname;

    private String avatarUrl;

    private String userProfile;

    private String userRole;

    private Integer status;
}
