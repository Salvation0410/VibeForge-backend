package com.yupi.yuaicodemother.model.dto.app;

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

    private Long id;

    /*
    * 应用名称
    * */
    private String appName;
}
