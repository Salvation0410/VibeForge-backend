package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

import java.nio.charset.StandardCharsets;
import java.util.Objects;
import java.util.UUID;

/** 根据固定配置、应用白名单和稳定比例选择生成引擎。 */
@Primary
@Component
@RequiredArgsConstructor
public class RoutingAiGenerationGateway implements AiGenerationGateway {
    private final AiEngineProperties properties;
    private final LegacyAiGenerationGateway legacy;
    private final LangGraphAiGenerationGateway langGraph;

    @Override
    public CodeGenTypeEnum route(String prompt, Long appId, Long userId, String requestId) {
        return delegate(appId).route(prompt, appId, userId, requestId);
    }

    @Override
    public Flux<String> generate(String prompt, CodeGenTypeEnum codeGenType, Long appId, Long userId, String requestId) {
        return delegate(appId).generate(prompt, codeGenType, appId, userId, requestId);
    }

    private AiGenerationGateway delegate(Long appId) {
        String engine = properties.getEngine();
        if ("langgraph".equalsIgnoreCase(engine)) return langGraph;
        if (!"gray".equalsIgnoreCase(engine)) return legacy;
        if (appId != null && properties.getGrayWhitelist().contains(appId)) return langGraph;
        int percentage = Math.max(0, Math.min(100, properties.getGrayPercentage()));
        if (percentage == 0) return legacy;
        if (percentage == 100) return langGraph;
        String key = Objects.toString(appId, "") + ":" + Objects.toString(properties.getGraySalt(), "ai");
        int bucket = Math.floorMod(UUID.nameUUIDFromBytes(key.getBytes(StandardCharsets.UTF_8)).hashCode(), 100);
        return bucket < percentage ? langGraph : legacy;
    }
}
