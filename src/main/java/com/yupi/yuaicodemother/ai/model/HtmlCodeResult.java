package com.yupi.yuaicodemother.ai.model;

import dev.langchain4j.model.output.structured.Description;
import lombok.Data;

/**
 * @author huang
 * @version 1.0
 * @description html代码结构化数据java对象
 * @date 2026/5/26
 */

@Data
@Description("生成 HTML 代码文件的结果")
public class HtmlCodeResult {

    @Description("HTML 代码")
    private String htmlCode;

    @Description("生成的 HTML代码 描述")
    private String description;
}
