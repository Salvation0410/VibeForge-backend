package com.yupi.yuaicodemother.model.vo;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/**
 * 登录态返回对象。
 */
@Data
public class LoginUserVO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    /**
     * 登录用户信息
     */
    private SysUserVO user;
}
