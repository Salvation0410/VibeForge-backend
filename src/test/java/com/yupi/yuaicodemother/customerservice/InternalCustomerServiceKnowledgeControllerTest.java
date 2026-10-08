package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.config.JsonConfig;
import com.yupi.yuaicodemother.controller.InternalCustomerServiceKnowledgeController;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.junit.jupiter.api.Test;
import org.springframework.http.MediaType;
import org.springframework.http.converter.json.MappingJackson2HttpMessageConverter;
import org.springframework.http.converter.json.Jackson2ObjectMapperBuilder;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class InternalCustomerServiceKnowledgeControllerTest {
    @Test
    void httpRoutesMatchPythonLeaseClientWithApiContextPath() throws Exception {
        var coordinator = mock(KnowledgeMutationCoordinator.class);
        var ai = new AiEngineProperties(); ai.setToken("token");
        when(coordinator.ready()).thenReturn(true);
        when(coordinator.validate(any())).thenReturn(new KnowledgeMutationCoordinator.Validation(
                true, true, "document:1", "op_1", "INDEX", 3, 2000000000));
        var mvc = MockMvcBuilders.standaloneSetup(
                new InternalCustomerServiceKnowledgeController(coordinator, ai))
                .setMessageConverters(new MappingJackson2HttpMessageConverter(
                        new JsonConfig().jacksonObjectMapper(new Jackson2ObjectMapperBuilder())))
                .build();
        mvc.perform(post("/api/internal/customer-service/knowledge-mutation-leases:validate")
                        .contextPath("/api")
                        .header("Authorization", "Bearer token")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("""
                                {"scope":"document:1","operationId":"op_1","operation":"INDEX",
                                 "fence":3,"expiresAt":2000000000,"proof":"proof"}
                                """))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(0))
                .andExpect(jsonPath("$.data.current").value(true))
                .andExpect(jsonPath("$.data.fence").isNumber())
                .andExpect(jsonPath("$.data.fence").value(3))
                .andExpect(jsonPath("$.data.expiresAt").isNumber())
                .andExpect(jsonPath("$.data.expiresAt").value(2000000000));
        verify(coordinator).validate(new KnowledgeMutationCoordinator.Lease(
                "document:1", "op_1", "INDEX", 3, 2000000000, "proof"));
        mvc.perform(get("/api/internal/customer-service/knowledge-mutation-leases/health")
                        .contextPath("/api").header("Authorization", "Bearer token"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.ready").value(true));
    }

    @Test
    void internalValidationPreservesIntegerPrecisionWithProductionJsonConfig() throws Exception {
        var mapper = new JsonConfig().jacksonObjectMapper(new Jackson2ObjectMapperBuilder());
        long fence = 9_007_199_254_740_993L;
        var validation = new KnowledgeMutationCoordinator.Validation(
                true, true, "document:1", "op_1", "INDEX", fence, 2000000000);
        var json = mapper.readTree(mapper.writeValueAsBytes(validation));
        assertTrue(json.get("fence").isIntegralNumber());
        assertEquals(fence, json.get("fence").longValue());
        assertTrue(json.get("expiresAt").isIntegralNumber());
        assertEquals(2000000000L, json.get("expiresAt").longValue());
        assertEquals("\"" + fence + "\"", mapper.writeValueAsString(fence));
    }

    @Test
    void requiresBearerAndReturnsExactValidationData() {
        var coordinator = mock(KnowledgeMutationCoordinator.class);
        var ai = new AiEngineProperties(); ai.setToken("token");
        var controller = new InternalCustomerServiceKnowledgeController(coordinator, ai);
        var request = new InternalCustomerServiceKnowledgeController.LeaseValidationRequest(
                "document:1", "op_1", "INDEX", 3, 2000000000, "proof");
        assertThrows(BusinessException.class, () -> controller.validate(null, request));
        var validation = new KnowledgeMutationCoordinator.Validation(true, true,
                "document:1", "op_1", "INDEX", 3, 2000000000);
        when(coordinator.validate(any())).thenReturn(validation);
        var response = controller.validate("Bearer token", request);
        assertSame(validation, response.getData());
        assertEquals(0, response.getCode());
    }

    @Test
    void healthIsReadOnly() {
        var coordinator = mock(KnowledgeMutationCoordinator.class);
        when(coordinator.ready()).thenReturn(true);
        var ai = new AiEngineProperties(); ai.setToken("token");
        var response = new InternalCustomerServiceKnowledgeController(coordinator, ai).health("Bearer token");
        assertEquals(Boolean.TRUE, response.getData().get("ready"));
        verify(coordinator, never()).acquire(any(), any(), any(), any());
    }
}
