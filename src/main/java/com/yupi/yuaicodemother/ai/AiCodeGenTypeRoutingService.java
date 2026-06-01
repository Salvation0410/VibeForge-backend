package com.yupi.yuaicodemother.ai;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import dev.langchain4j.service.SystemMessage;

/**
 * @author huang
 * @version 1.0
 * @description Ai代码生成类型智能路由服务
 * @date 2026/6/1
 */
public interface AiCodeGenTypeRoutingService {

    /**
     * 根据用户需求智能选择代码生成类型
     *
     * @param userPrompt 用户输入的需求描述
     * @return 推荐的代码生成类型
     */
    @SystemMessage(fromResource = "prompt/codegen-routing-system-prompt.txt")
    CodeGenTypeEnum routeCodeGenType(String userPrompt);
}