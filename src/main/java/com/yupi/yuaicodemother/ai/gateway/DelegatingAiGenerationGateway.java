package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

import java.util.Objects;

/** 根据配置和用户灰度规则选择生成引擎。 */
@Primary
@Component
@RequiredArgsConstructor
public class DelegatingAiGenerationGateway implements AiGenerationGateway {
    private final AiEngineProperties properties;
    private final LegacyAiGenerationGateway legacy;
    private final LangGraphAiGenerationGateway langGraph;

    @Override
    public CodeGenTypeEnum route(String prompt, Long appId, Long userId, String requestId) {
        return delegate(userId, requestId).route(prompt, appId, userId, requestId);
    }

    @Override
    public Flux<String> generate(String prompt, CodeGenTypeEnum codeGenType, Long appId, Long userId, String requestId) {
        return delegate(userId, requestId).generate(prompt, codeGenType, appId, userId, requestId);
    }

    private AiGenerationGateway delegate(Long userId, String requestId) {
        String engine = Objects.toString(properties.getEngine(), "legacy").toLowerCase();
        if ("langgraph".equals(engine)) return langGraph;
        if ("gray".equals(engine) || "auto".equals(engine)) {
            if (userId != null && properties.getGrayWhitelist().contains(userId)) return langGraph;
            int percentage = Math.max(0, Math.min(100, properties.getGrayPercentage()));
            if (percentage > 0) {
                int bucket = Math.floorMod(Objects.hash(userId, requestId), 100);
                if (bucket < percentage) return langGraph;
            }
        }
        return legacy;
    }
}
