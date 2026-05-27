package com.yupi.yuaicodemother.model.dto.app;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

/**
 * 创建应用请求
 */
@Data
public class AppAddRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private String appName;

    private String cover;

    /*
    * 应用初始化的prompt
    * */
    private String initPrompt;

    private String codeGenType;
}
