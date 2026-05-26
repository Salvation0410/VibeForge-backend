package com.yupi.yuaicodemother.ai.model;

import dev.langchain4j.model.output.structured.Description;
import lombok.Data;

/**
 * @author huang
 * @version 1.0
 * @description 三件套结构化输出对象
 * @date 2026/5/26
 */
@Data
@Description("生成多个文件代码的结果")
public class MultiFileCodeResult {

    @Description("HTML代码")
    private String htmlCode;

    @Description("CSS代码")
    private String cssCode;

    @Description("JS代码")
    private String jsCode;

    @Description("生成代码的描述")
    private String description;
}
