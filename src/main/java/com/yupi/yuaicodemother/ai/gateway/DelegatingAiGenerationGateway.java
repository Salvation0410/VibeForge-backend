package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.RequiredArgsConstructor;
import org.springframework.context.annotation.Primary;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
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
        return delegate(appId, userId, requestId).route(prompt, appId, userId, requestId);
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
        return delegate(appId, userId, requestId).generate(prompt, codeGenType, appId, userId, requestId);
    }

    /** 将取消请求发送给本次请求确定性选中的同一生成引擎。 */
    @Override
    public void cancel(Long appId, Long userId, String requestId) {
        delegate(appId, userId, requestId).cancel(appId, userId, requestId);
    }

    /**
     * 根据引擎配置选择具体网关。
     * <p>
     * {@code langgraph} 表示全量使用新引擎；{@code gray} 或 {@code auto} 先匹配用户白名单，
     * 再通过稳定业务主体与灰度盐计算灰度桶；其他配置均回退到 Legacy 引擎。
     *
     * @param appId 当前应用 ID，可为空
     * @param userId 当前用户 ID，可为空
     * @param requestId 本次请求唯一标识
     * @return 本次请求实际使用的 AI 生成网关
     */
    private AiGenerationGateway delegate(Long appId, Long userId, String requestId) {
        String engine = Objects.toString(properties.getEngine(), "legacy").toLowerCase();
        if ("langgraph".equals(engine)) return langGraph;
        if ("gray".equals(engine) || "auto".equals(engine)) {
            if (userId != null && properties.getGrayWhitelist().contains(userId)) return langGraph;
            int percentage = Math.max(0, Math.min(100, properties.getGrayPercentage()));
            if (percentage > 0 && stableBucket(appId, userId, requestId) < percentage) return langGraph;
        }
        return legacy;
    }

    private int stableBucket(Long appId, Long userId, String requestId) {
        String subject = userId != null ? "user:" + userId
                : appId != null ? "app:" + appId
                : "request:" + Objects.toString(requestId, "");
        String value = Objects.toString(properties.getGraySalt(), "") + ":" + subject;
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest(value.getBytes(StandardCharsets.UTF_8));
            return (ByteBuffer.wrap(digest, 0, Integer.BYTES).getInt() & Integer.MAX_VALUE) % 100;
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException("SHA-256 is unavailable", error);
        }
    }
}
