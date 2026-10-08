package com.yupi.yuaicodemother.ai;

import com.yupi.yuaicodemother.utils.SpringContextUtil;
import dev.langchain4j.model.chat.ChatModel;
import dev.langchain4j.service.AiServices;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Scope;

/**
 * AI 代码生成类型路由服务工厂。
 */
@Configuration
@Slf4j
public class AiCodeGenTypeRoutingServiceFactory {

    private static final String ROUTING_CHAT_MODEL_PROTOTYPE = "routingChatModelPrototype";


    /**
     * 创建 AI服务
     * @return
     */
    public AiCodeGenTypeRoutingService createAiCodeGenTypeRoutingService() {
        ChatModel routingChatModel = SpringContextUtil.getBean(ROUTING_CHAT_MODEL_PROTOTYPE, ChatModel.class);
        log.info("Create routing AI service, thread: {}, chatModel: {}@{}",
                Thread.currentThread().getName(),
                routingChatModel.getClass().getName(),
                System.identityHashCode(routingChatModel));

        return AiServices.builder(AiCodeGenTypeRoutingService.class)
                .chatModel(routingChatModel)
                .build();
    }

    /**
     * 兼容老逻辑 默认提供一个Bean
     * @return
     */
    @Bean
    //@Scope("prototype")
    public AiCodeGenTypeRoutingService aiCodeGenTypeRoutingService() {
        return createAiCodeGenTypeRoutingService();
    }
}
