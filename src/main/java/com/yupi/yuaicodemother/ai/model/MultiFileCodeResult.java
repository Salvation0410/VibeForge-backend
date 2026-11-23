package com.yupi.yuaicodemother.ai.model;

import lombok.Data;

/**
 * @author huang
 * @version 1.0
 * @description 三件套结构化输出对象
 * @date 2026/5/26
 */
@Data
public class MultiFileCodeResult {

    private String htmlCode;

    private String cssCode;

    private String jsCode;

    private String description;
}
