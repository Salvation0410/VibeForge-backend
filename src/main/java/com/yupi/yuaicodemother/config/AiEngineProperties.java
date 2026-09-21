package com.yupi.yuaicodemother.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.ArrayList;
import java.util.List;

/**
 * LangGraph AI 服务连接、内部鉴权和灰度切换配置，对应 {@code ai.*} 配置项。
 */
@Data
@ConfigurationProperties(prefix = "ai")
public class AiEngineProperties {
    /** AI 引擎模式，可选 {@code legacy}、{@code langgraph}、{@code gray} 或 {@code auto}。 */
    private String engine = "legacy";
    /** Python LangGraph AI 服务基础地址。 */
    private String serviceUrl = "http://localhost:8000";
    /** Spring 与 Python 服务双向内部调用使用的共享 Bearer 令牌。 */
    private String token = "";
    /** 灰度模式下强制使用 LangGraph 的用户 ID 白名单。 */
    private List<Long> grayWhitelist = new ArrayList<>();
    /** 灰度模式下进入 LangGraph 的流量百分比，运行时会限制在 0 到 100。 */
    private int grayPercentage = 0;
    /** 预留的灰度散列盐配置。 */
    private String graySalt = "ai-generation";
    /** 内部工具成功结果和状态的 Redis 保留时间，单位为秒。 */
    private long toolIdempotencyTtlSeconds = 86400;
    /** 等待同一工具调用分布式锁的最长时间，单位为毫秒。 */
    private long toolIdempotencyLockWaitMillis = 30000;
}
