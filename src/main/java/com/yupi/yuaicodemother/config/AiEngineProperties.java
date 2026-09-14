package com.yupi.yuaicodemother.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

import java.util.ArrayList;
import java.util.List;

/** LangGraph AI 服务及灰度切换配置。 */
@Data
@ConfigurationProperties(prefix = "ai")
public class AiEngineProperties {
    /** legacy、langgraph 或 gray。 */
    private String engine = "legacy";
    private String serviceUrl = "http://localhost:8000";
    private String token = "";
    private List<Long> grayWhitelist = new ArrayList<>();
    private int grayPercentage = 0;
    private String graySalt = "ai-generation";
}
