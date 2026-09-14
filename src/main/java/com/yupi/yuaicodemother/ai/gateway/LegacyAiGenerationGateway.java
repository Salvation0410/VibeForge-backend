package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.ai.AiCodeGenTypeRoutingService;
import com.yupi.yuaicodemother.ai.AiCodeGenTypeRoutingServiceFactory;
import com.yupi.yuaicodemother.core.AiCodeGeneratorFacade;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

/** 现有 LangChain4j 生成链路的网关适配。 */
@Component
@RequiredArgsConstructor
public class LegacyAiGenerationGateway implements AiGenerationGateway {
    private final AiCodeGeneratorFacade facade;
    private final AiCodeGenTypeRoutingServiceFactory routingFactory;

    @Override
    public CodeGenTypeEnum route(String prompt, Long appId, Long userId, String requestId) {
        AiCodeGenTypeRoutingService service = routingFactory.createAiCodeGenTypeRoutingService();
        return service.routeCodeGenType(prompt);
    }

    @Override
    public Flux<String> generate(String prompt, CodeGenTypeEnum codeGenType, Long appId, Long userId, String requestId) {
        return facade.generateAndSaveCodeStream(prompt, codeGenType, appId);
    }
}
