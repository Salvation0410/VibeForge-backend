package com.yupi.yuaicodemother.exception;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import static org.junit.jupiter.api.Assertions.*;

/** JSON 子资源的鉴权错误不能被共享路径前缀误判为 SSE。 */
class GenerationJsonErrorBoundaryTest {
    @AfterEach
    void clearContext() {
        RequestContextHolder.resetRequestAttributes();
    }

    @ParameterizedTest
    @ValueSource(strings = {"task", "status", "cancel"})
    void generationSubresourceKeepsJsonErrorEvenWithSseAccept(String resource) throws Exception {
        var request = new MockHttpServletRequest("GET", "/api/apps/chat/gen/code/" + resource);
        request.addHeader("Accept", "text/event-stream");
        var response = new MockHttpServletResponse();
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request, response));
        var result = new GlobalExceptionHandler().businessExceptionHandler(new BusinessException(ErrorCode.NO_AUTH_ERROR));
        assertNotNull(result);
        assertEquals(ErrorCode.NO_AUTH_ERROR.getCode(), result.getCode());
        assertEquals("", response.getContentAsString());
        assertNull(response.getContentType());
    }

    @Test
    void actualGenerationStreamRetainsSseErrorWithoutAcceptHeader() throws Exception {
        var request = new MockHttpServletRequest("GET", "/api/apps/chat/gen/code");
        var response = new MockHttpServletResponse();
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request, response));
        assertNull(new GlobalExceptionHandler().businessExceptionHandler(new BusinessException(ErrorCode.NO_AUTH_ERROR)));
        assertTrue(response.getContentType().startsWith("text/event-stream"));
        assertTrue(response.getContentAsString().contains("40101"));
    }
}
