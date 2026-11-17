package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.controller.InternalKnowledgePublicationController;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.service.KnowledgePublicationService;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class KnowledgeInventoryControllerTest {
    @Test void inventoryAuthenticatesBeforeReading() {
        var service=mock(KnowledgePublicationService.class);var config=new AiEngineProperties();config.setToken("isolated-secret");
        var controller=new InternalKnowledgePublicationController(service,config);
        assertThrows(Exception.class,()->controller.inventory(null,0,100));
        assertThrows(Exception.class,()->controller.inventory("Bearer wrong",0,100));
        verifyNoInteractions(service);
        controller.inventory("Bearer isolated-secret",2,10);
        verify(service).inventory(2,10);
    }
}
