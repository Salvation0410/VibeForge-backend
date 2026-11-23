package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.AiEngineConfig;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.context.annotation.ComponentScan;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.FilterType;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.mock;

class PythonOnlyGatewayConfigurationTest {
    @Test
    void componentScanRegistersOnlyPythonGatewayWithoutProviderCredentials() {
        // 使用实际组件扫描发现所有网关实现，仅用替身替换业务上下文，避免数据库与模型调用。
        new ApplicationContextRunner()
                .withUserConfiguration(GatewayScan.class, AiEngineConfig.class)
                .withBean(ObjectMapper.class, ObjectMapper::new)
                .withBean(GenerationInputContextProvider.class, () -> mock(GenerationInputContextProvider.class))
                .withPropertyValues("ai.service-url=http://127.0.0.1:1", "ai.token=test-internal-token",
                        "ai.engine=legacy", "ai.gray-percentage=0")
                .run(context -> {
                    assertNull(context.getStartupFailure());
                    var gateways = context.getBeansOfType(AiGenerationGateway.class);
                    assertEquals(1, gateways.size());
                    assertInstanceOf(LangGraphAiGenerationGateway.class, gateways.values().iterator().next());
                    assertEquals("http://127.0.0.1:1", context.getBean(AiEngineProperties.class).getServiceUrl());
                });
    }

    @Test
    void javaModelFrameworksAreAbsentFromRuntimeClasspath() {
        // 检查运行时而非源码字符串，防止 Maven 传递依赖意外恢复旧模型框架。
        for (String name : new String[]{"dev.langchain4j.model.chat.ChatModel",
                "org.bsc.langgraph4j.StateGraph", "com.alibaba.dashscope.aigc.generation.Generation"}) {
            assertThrows(ClassNotFoundException.class, () -> Class.forName(name), name);
        }
    }

    @Configuration(proxyBeanMethods = false)
    @ComponentScan(basePackageClasses = AiGenerationGateway.class, useDefaultFilters = false,
            includeFilters = @ComponentScan.Filter(type = FilterType.ASSIGNABLE_TYPE, classes = AiGenerationGateway.class))
    static class GatewayScan {
    }
}
