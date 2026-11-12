package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.ai.gateway.GenerationStreamException;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.dto.app.StructuredEditIntent;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.service.AppService;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Flux;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class AppControllerSseTest {
    @Test
    void structuredQueryIsValidatedAndForwardedWithoutChangingSse() throws Exception {
        var controller = new AppController();
        var service = mock(AppService.class);
        var userService = mock(SysUserService.class);
        var request = mock(HttpServletRequest.class);
        var user = SysUser.builder().id(7L).build();
        ReflectionTestUtils.setField(controller, "appService", service);
        ReflectionTestUtils.setField(controller, "userService", userService);
        when(userService.getLoginUser(request)).thenReturn(user);
        var intent = new StructuredEditIntent("1", "body > p:nth-of-type(1)", "fontSize", "24px");
        String json = new ObjectMapper().writeValueAsString(intent);
        when(service.chatToGenCode(42L, intent.canonicalMessage(), user, intent)).thenReturn(Flux.just("chunk"));
        var events = controller.chatToGenCode(42L, intent.canonicalMessage(), json, request).collectList().block();
        assertEquals("done", events.getLast().event());
        verify(service).chatToGenCode(42L, intent.canonicalMessage(), user, intent);
        clearInvocations(service);
        assertThrows(BusinessException.class,
                () -> controller.chatToGenCode(42L, intent.canonicalMessage() + "额外修改", json, request));
        assertThrows(BusinessException.class,
                () -> controller.chatToGenCode(42L, intent.canonicalMessage(), " ".repeat(2049), request));
        verifyNoInteractions(service);
    }

    @Test
    void successEndsWithDone() {
        var fixture = fixture(Flux.just("chunk"));
        var events = fixture.controller.chatToGenCode(42L, "build", fixture.request).collectList().block();
        assertNotNull(events); assertEquals(2, events.size());
        assertNull(events.getFirst().event()); assertEquals("done", events.getLast().event());
    }

    @ParameterizedTest
    @ValueSource(strings = {"MODEL_OUTPUT_TRUNCATED", "QUALITY_REVIEW_INVALID_OUTPUT",
            "QUALITY_REVIEW_INCOMPLETE", "REPAIR_NO_PROGRESS", "QUALITY_REVIEW_FAILED",
            "ARTIFACT_VALIDATION_FAILED", "VUE_ANCHOR_TARGET_MISSING", "VUE_ANCHOR_CHECK_UNAVAILABLE"})
    void failureEmitsBusinessErrorWithoutDoneOrNamedTransportError(String errorCode) {
        var error = new GenerationStreamException(50001, errorCode, "req-1",
                "本轮生成已停止", null);
        var fixture = fixture(Flux.error(error));
        var events = fixture.controller.chatToGenCode(42L, "build", fixture.request).collectList().block();
        assertNotNull(events);
        assertEquals(1, events.size());
        assertEquals("business-error", events.getFirst().event());
        assertTrue(events.stream().noneMatch(event -> "done".equals(event.event())));
        assertTrue(events.stream().noneMatch(event -> "error".equals(event.event())));
        assertTrue(events.getFirst().data().contains(errorCode));
        assertTrue(events.getFirst().data().contains("req-1"));
    }

    private Fixture fixture(Flux<String> stream) {
        AppController controller = new AppController();
        AppService appService = mock(AppService.class);
        SysUserService userService = mock(SysUserService.class);
        HttpServletRequest request = mock(HttpServletRequest.class);
        SysUser user = new SysUser(); user.setId(7L);
        when(userService.getLoginUser(request)).thenReturn(user);
        when(appService.chatToGenCode(42L, "build", user)).thenReturn(stream);
        ReflectionTestUtils.setField(controller, "appService", appService);
        ReflectionTestUtils.setField(controller, "userService", userService);
        return new Fixture(controller, request);
    }

    private record Fixture(AppController controller, HttpServletRequest request) { }
}
