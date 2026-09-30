package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.controller.CustomerServiceKnowledgeAdminController;
import com.yupi.yuaicodemother.model.vo.CustomerServiceKnowledgeDocumentVO;
import org.junit.jupiter.api.Test;

import java.util.Arrays;

import static org.junit.jupiter.api.Assertions.*;

class CustomerServiceKnowledgeAdminControllerTest {
    @Test
    void everyEndpointRequiresAdminAndVoDoesNotExposeObjectStorage() {
        Arrays.stream(CustomerServiceKnowledgeAdminController.class.getDeclaredMethods())
                .filter(method -> java.lang.reflect.Modifier.isPublic(method.getModifiers()))
                .forEach(method -> {
                    AuthCheck auth = method.getAnnotation(AuthCheck.class);
                    assertNotNull(auth, method.getName());
                    assertEquals(UserConstant.ADMIN_ROLE, auth.mustRole());
                });
        assertThrows(NoSuchFieldException.class, () -> CustomerServiceKnowledgeDocumentVO.class.getDeclaredField("objectKey"));
        assertThrows(NoSuchFieldException.class, () -> CustomerServiceKnowledgeDocumentVO.class.getDeclaredField("signedUrl"));
        assertThrows(NoSuchFieldException.class, () -> CustomerServiceKnowledgeDocumentVO.class.getDeclaredField("proof"));
    }
}
