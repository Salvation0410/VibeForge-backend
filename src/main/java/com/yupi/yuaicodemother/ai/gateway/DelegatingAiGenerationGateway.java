package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

import java.util.Objects;

/**
 * AI 生成网关的主委派实现，根据固定配置、用户白名单和灰度比例选择实际引擎。
 */
@Primary
@Component
@RequiredArgsConstructor
public class DelegatingAiGenerationGateway implements AiGenerationGateway {
    private final AiEngineProperties properties;
    private final LegacyAiGenerationGateway legacy;
    private final LangGraphAiGenerationGateway langGraph;

    /**
     * 选择本次请求对应的引擎，并委派代码生成类型路由。
     *
     * @param prompt 用户输入的应用生成需求
     * @param appId 应用 ID；创建阶段可以为空
     * @param userId 当前用户 ID
     * @param requestId 本次路由请求的唯一标识
     * @return 被选中引擎判断出的代码生成类型
     */
    @Override
    public CodeGenTypeEnum route(String prompt, Long appId, Long userId, String requestId) {
        return delegate(userId, requestId).route(prompt, appId, userId, requestId);
    }

    /**
     * 选择本次请求对应的引擎，并委派流式代码生成。
     *
     * @param prompt 用户输入的代码生成或修改需求
     * @param codeGenType 应用代码生成类型
     * @param appId 应用 ID
     * @param userId 当前用户 ID
     * @param requestId 本次生成请求的唯一标识
     * @return 可被现有流处理器消费的字符串数据流
     */
    @Override
    public Flux<String> generate(String prompt, CodeGenTypeEnum codeGenType, Long appId, Long userId, String requestId) {
        return delegate(userId, requestId).generate(prompt, codeGenType, appId, userId, requestId);
    }

    /**
     * 根据引擎配置选择具体网关。
     * <p>
     * {@code langgraph} 表示全量使用新引擎；{@code gray} 或 {@code auto} 先匹配用户白名单，
     * 再通过用户 ID 与请求 ID 计算灰度桶；其他配置均回退到 Legacy 引擎。
     *
     * @param userId 当前用户 ID，可为空
     * @param requestId 本次请求唯一标识
     * @return 本次请求实际使用的 AI 生成网关
     */
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
