package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.Test;
import reactor.core.publisher.Flux;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

class DelegatingAiGenerationGatewayTest {
    @Test
    void sameUserUsesSameEngineAcrossRequestIds() {
        AiEngineProperties properties = properties("gray", 50, "stable-test");
        LegacyAiGenerationGateway legacy = mock(LegacyAiGenerationGateway.class);
        LangGraphAiGenerationGateway langGraph = mock(LangGraphAiGenerationGateway.class);
        when(legacy.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.HTML);
        when(langGraph.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.VUE_PROJECT);
        DelegatingAiGenerationGateway gateway = new DelegatingAiGenerationGateway(properties, legacy, langGraph);

        CodeGenTypeEnum first = gateway.route("build", 1L, 42L, "request-a");
        CodeGenTypeEnum second = gateway.route("build", 2L, 42L, "request-b");

        assertEquals(first, second);
        verify(legacy, times(first == CodeGenTypeEnum.HTML ? 2 : 0))
                .route(anyString(), any(), any(), anyString());
        verify(langGraph, times(first == CodeGenTypeEnum.VUE_PROJECT ? 2 : 0))
                .route(anyString(), any(), any(), anyString());
    }

    @Test
    void routeGenerateAndCancelUseSameEngine() {
        AiEngineProperties properties = properties("gray", 50, "consistent-test");
        LegacyAiGenerationGateway legacy = mock(LegacyAiGenerationGateway.class);
        LangGraphAiGenerationGateway langGraph = mock(LangGraphAiGenerationGateway.class);
        when(legacy.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.HTML);
        when(langGraph.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.HTML);
        when(legacy.generate(anyString(), any(), any(), any(), anyString())).thenReturn(Flux.empty());
        when(langGraph.generate(anyString(), any(), any(), any(), anyString())).thenReturn(Flux.empty());
        DelegatingAiGenerationGateway gateway = new DelegatingAiGenerationGateway(properties, legacy, langGraph);

        gateway.route("build", 7L, 42L, "req-1");
        gateway.generate("build", CodeGenTypeEnum.HTML, 7L, 42L, "req-1").blockLast();
        gateway.cancel(7L, 42L, "req-1");

        verify(legacy, atMostOnce()).route(anyString(), any(), any(), anyString());
        verify(langGraph, atMostOnce()).route(anyString(), any(), any(), anyString());
        assertEquals(1, mockingDetails(legacy).getInvocations().stream()
                .filter(invocation -> invocation.getMethod().getName().equals("cancel")).count()
                + mockingDetails(langGraph).getInvocations().stream()
                .filter(invocation -> invocation.getMethod().getName().equals("cancel")).count());
    }

    @Test
    void whitelistAndPercentageBoundariesAreDeterministic() {
        AiEngineProperties properties = properties("gray", 0, "boundary-test");
        properties.setGrayWhitelist(java.util.List.of(99L));
        LegacyAiGenerationGateway legacy = mock(LegacyAiGenerationGateway.class);
        LangGraphAiGenerationGateway langGraph = mock(LangGraphAiGenerationGateway.class);
        when(legacy.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.HTML);
        when(langGraph.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.VUE_PROJECT);
        DelegatingAiGenerationGateway gateway = new DelegatingAiGenerationGateway(properties, legacy, langGraph);

        assertEquals(CodeGenTypeEnum.VUE_PROJECT, gateway.route("x", 1L, 99L, "r"));
        assertEquals(CodeGenTypeEnum.HTML, gateway.route("x", 1L, 100L, "r"));
        properties.setGrayPercentage(100);
        assertEquals(CodeGenTypeEnum.VUE_PROJECT, gateway.route("x", 1L, 100L, "r2"));
    }

    @Test
    void changedSaltCanChangeAssignment() {
        LegacyAiGenerationGateway legacy = mock(LegacyAiGenerationGateway.class);
        LangGraphAiGenerationGateway langGraph = mock(LangGraphAiGenerationGateway.class);
        when(legacy.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.HTML);
        when(langGraph.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.VUE_PROJECT);
        boolean sawLegacy = false;
        boolean sawLangGraph = false;
        for (int i = 0; i < 200; i++) {
            AiEngineProperties properties = properties("gray", 50, "salt-" + i);
            CodeGenTypeEnum selected = new DelegatingAiGenerationGateway(properties, legacy, langGraph)
                    .route("x", 1L, 42L, "different-request");
            sawLegacy |= selected == CodeGenTypeEnum.HTML;
            sawLangGraph |= selected == CodeGenTypeEnum.VUE_PROJECT;
        }
        assertTrue(sawLegacy && sawLangGraph);
    }

    @Test
    void fixedAndUnknownEnginesPreserveLegacyCompatibility() {
        LegacyAiGenerationGateway legacy = mock(LegacyAiGenerationGateway.class);
        LangGraphAiGenerationGateway langGraph = mock(LangGraphAiGenerationGateway.class);
        when(legacy.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.HTML);
        when(langGraph.route(anyString(), any(), any(), anyString())).thenReturn(CodeGenTypeEnum.VUE_PROJECT);
        AiEngineProperties properties = properties("langgraph", 0, "x");
        DelegatingAiGenerationGateway gateway = new DelegatingAiGenerationGateway(properties, legacy, langGraph);
        assertEquals(CodeGenTypeEnum.VUE_PROJECT, gateway.route("x", null, null, "r"));
        properties.setEngine("other");
        assertEquals(CodeGenTypeEnum.HTML, gateway.route("x", null, null, "r"));
    }

    private AiEngineProperties properties(String engine, int percentage, String salt) {
        AiEngineProperties properties = new AiEngineProperties();
        properties.setEngine(engine);
        properties.setGrayPercentage(percentage);
        properties.setGraySalt(salt);
        return properties;
    }
}
