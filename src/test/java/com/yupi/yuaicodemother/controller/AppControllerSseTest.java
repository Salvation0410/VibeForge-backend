package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.ai.gateway.GenerationStreamException;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.AppService;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Flux;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class AppControllerSseTest {
    @Test
    void successEndsWithDone() {
        var fixture = fixture(Flux.just("chunk"));
        var events = fixture.controller.chatToGenCode(42L, "build", fixture.request).collectList().block();
        assertNotNull(events); assertEquals(2, events.size());
        assertNull(events.getFirst().event()); assertEquals("done", events.getLast().event());
    }

    @Test
    void failureEmitsErrorWithoutDone() {
        var error = new GenerationStreamException(50001, "MODEL_OUTPUT_TRUNCATED", "req-1",
                "模型输出达到长度限制，已保留上一版本", null);
        var fixture = fixture(Flux.error(error));
        var events = fixture.controller.chatToGenCode(42L, "build", fixture.request).collectList().block();
        assertNotNull(events); assertEquals(1, events.size()); assertEquals("error", events.getFirst().event());
        assertTrue(events.getFirst().data().contains("MODEL_OUTPUT_TRUNCATED"));
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
