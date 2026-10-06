package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.controller.InternalCustomerServiceKnowledgeController;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class InternalCustomerServiceKnowledgeControllerTest {
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
