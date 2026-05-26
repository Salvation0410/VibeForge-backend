package com.yupi.yuaicodemother.model.dto;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/**
 * 用户更新应用请求
 */
@Data
public class AppUserUpdateRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private String appName;
}
