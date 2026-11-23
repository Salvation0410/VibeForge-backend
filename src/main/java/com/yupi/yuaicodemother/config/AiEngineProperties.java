package com.yupi.yuaicodemother.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Python AI 服务连接与内部鉴权配置，对应 {@code ai.*} 配置项。
 */
@Data
@ConfigurationProperties(prefix = "ai")
public class AiEngineProperties {
    /** Python LangGraph AI 服务基础地址。 */
    private String serviceUrl = "http://localhost:8000";
    /** Spring 与 Python 服务双向内部调用使用的共享 Bearer 令牌。 */
    private String token = "";
    /** LangGraph NDJSON 响应连续无完整事件的最长等待时间，单位为秒。 */
    private long generationStreamIdleTimeoutSeconds = 600;
    /** 内部工具成功结果和状态的 Redis 保留时间，单位为秒。 */
    private long toolIdempotencyTtlSeconds = 86400;
    /** 等待同一工具调用分布式锁的最长时间，单位为毫秒。 */
    private long toolIdempotencyLockWaitMillis = 30000;
}
