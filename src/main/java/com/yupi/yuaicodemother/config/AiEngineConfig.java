package com.yupi.yuaicodemother.config;

import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;

/**
 * 启用 AI 引擎配置属性绑定，使网关和内部工具接口可以注入统一配置。
 */
@Configuration
@EnableConfigurationProperties(AiEngineProperties.class)
public class AiEngineConfig {
}
