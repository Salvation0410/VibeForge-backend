package com.yupi.yuaicodemother.model.dto.app;

import lombok.Data;

/**
 * @author huang
 * @version 1.0
 * @description 部署请求类
 * @date 2026/5/27
 */
@Data
public class AppDeployRequest {

    /**
     * 应用 id
     */
    private Long appId;

    private static final long serialVersionUID = 1L;
}
