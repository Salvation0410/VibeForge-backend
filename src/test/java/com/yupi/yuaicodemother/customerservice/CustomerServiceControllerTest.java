package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.model.dto.customerservice.CustomerServiceAskRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.CustomerServiceAnswerVO;
import com.yupi.yuaicodemother.service.CustomerServiceAnswerService;
import com.yupi.yuaicodemother.service.SysUserService;
import com.yupi.yuaicodemother.controller.CustomerServiceController;
import jakarta.servlet.http.HttpServletRequest;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.*;
import com.yupi.yuaicodemother.ratelimit.annotation.RateLimit;
import org.springframework.web.bind.annotation.PostMapping;
import com.fasterxml.jackson.databind.ObjectMapper;

class CustomerServiceControllerTest {
    @Test
    void anonymousRequestUsesExistingLoginGuard() {
        CustomerServiceAnswerService answer = mock(CustomerServiceAnswerService.class);
        SysUserService users = mock(SysUserService.class);
        HttpServletRequest request = mock(HttpServletRequest.class);
        when(users.getLoginUser(request)).thenThrow(new RuntimeException("not login"));
        CustomerServiceController controller = new CustomerServiceController(answer, users);
        CustomerServiceAskRequest body = new CustomerServiceAskRequest(); body.setQuestion("hello");
        assertThrows(RuntimeException.class, () -> controller.ask(body, request));
        verifyNoInteractions(answer);
    }

    @Test
    void loggedInRequestCallsServiceOnceAndPublicShapeIsStable() {
        CustomerServiceAnswerService answer = mock(CustomerServiceAnswerService.class);
        SysUserService users = mock(SysUserService.class);
        HttpServletRequest request = mock(HttpServletRequest.class);
        SysUser user = new SysUser(); user.setId(9L);
        when(users.getLoginUser(request)).thenReturn(user);
        when(answer.answer(any(), eq(9L))).thenReturn(new CustomerServiceAnswerVO(true, "answer", java.util.List.of()));
        CustomerServiceController controller = new CustomerServiceController(answer, users);
        CustomerServiceAskRequest body = new CustomerServiceAskRequest(); body.setQuestion("hello");
        var response = controller.ask(body, request);
        verify(answer, times(1)).answer(body, 9L);
        String json = cn.hutool.json.JSONUtil.toJsonStr(response.getData());
        org.junit.jupiter.api.Assertions.assertFalse(json.contains("degraded"));
        org.junit.jupiter.api.Assertions.assertFalse(json.contains("score"));
    }

    @Test
    void routeIsUserRateLimitedAndDtoRejectsExtraFields() throws Exception {
        var method = CustomerServiceController.class.getMethod("ask", CustomerServiceAskRequest.class,
                HttpServletRequest.class);
        org.junit.jupiter.api.Assertions.assertEquals("/ask", method.getAnnotation(PostMapping.class).value()[0]);
        org.junit.jupiter.api.Assertions.assertEquals(com.yupi.yuaicodemother.ratelimit.enums.RateLimitType.USER,
                method.getAnnotation(RateLimit.class).limitType());
        assertThrows(Exception.class, () -> new ObjectMapper().readValue(
                "{\"question\":\"hello\",\"score\":1}", CustomerServiceAskRequest.class));
    }
}
