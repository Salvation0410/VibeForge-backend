package com.yupi.yuaicodemother.ai;

import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.service.AiServices;
import jakarta.annotation.Resource;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

/**
 * @author huang
 * @version 1.0
 * @description AI service创建工厂类
 * @date 2026/5/26
 */
@Configuration
public class AiCodeGeneratorServiceFactory {

    // 这里使用的是的openai的模式 deepSeek兼容openai
    @Resource
    private ChatModel chatModel;

    /*
    * 创建AI代码生成器服务
    * */

    @Bean
    public AiCodeGeneratorService aiCodeGeneratorService() {
        return AiServices.create(AiCodeGeneratorService.class, chatModel);
    }
}
