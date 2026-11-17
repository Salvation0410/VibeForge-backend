package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.aop.AuthInterceptor;
import com.yupi.yuaicodemother.controller.CustomerServiceKnowledgeAdminController;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.mapper.KnowledgePublicationMapper;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePrepareObservationRow;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.KnowledgePrepareObservationService;
import com.yupi.yuaicodemother.service.SysUserService;
import org.junit.jupiter.api.Test;
import org.springframework.aop.aspectj.annotation.AspectJProxyFactory;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;
import java.time.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class KnowledgePrepareObservationTest {
    static final Instant NOW=Instant.parse("2030-01-01T00:00:00Z");
    final KnowledgePublicationMapper mapper=mock(KnowledgePublicationMapper.class);
    final KnowledgePrepareObservationService service=new KnowledgePrepareObservationService(mapper,Clock.fixed(NOW,ZoneOffset.UTC));

    @Test void stateProjectionHasExactDeadlineBoundaryAndNoWrites() {
        var row=new KnowledgePrepareObservationRow();row.setTaskId(1);
        when(mapper.observePrepareExecution(2,1)).thenReturn(row);
        assertEquals("NOT_REGISTERED",service.observe(2,1).observationState());
        row.setStatus("STARTED");row.setObservationDeadline(LocalDateTime.ofInstant(NOW.plusSeconds(1),ZoneOffset.UTC));
        assertEquals("BUSY",service.observe(2,1).observationState());
        row.setObservationDeadline(LocalDateTime.ofInstant(NOW,ZoneOffset.UTC));
        assertEquals("OBSERVATION_EXPIRED",service.observe(2,1).reasonCode());
        assertEquals("STARTED",row.getStatus());
        row.setStatus("UNKNOWN");assertEquals("EXECUTION_UNCONFIRMED",service.observe(2,1).reasonCode());
        row.setStatus("COMPLETE");assertEquals("COMPLETION_RECORDED",service.observe(2,1).reasonCode());
        row.setStatus("private-text");assertEquals("INVALID",service.observe(2,1).recordedState());
        row.setStatus("STARTED");row.setObservationDeadline(null);
        assertEquals("INVALID_EXECUTION_STATE",service.observe(2,1).reasonCode());
        verify(mapper,times(7)).observePrepareExecution(2,1);verifyNoMoreInteractions(mapper);
    }
    @Test void invalidMissingAndDatabaseFailureAreNotUnregistered() {
        assertEquals(ErrorCode.PARAMS_ERROR.getCode(),assertThrows(BusinessException.class,()->service.observe(0,1)).getCode());
        verifyNoInteractions(mapper);
        assertEquals(ErrorCode.NOT_FOUND_ERROR.getCode(),assertThrows(BusinessException.class,()->service.observe(2,1)).getCode());
        when(mapper.observePrepareExecution(2,1)).thenThrow(new org.springframework.dao.DataAccessResourceFailureException("offline"));
        assertThrows(org.springframework.dao.DataAccessResourceFailureException.class,()->service.observe(2,1));
    }
    @Test void realPermissionAspectRejectsUserAndAllowsAdminWithBoundedJson() throws Exception {
        var controller=new CustomerServiceKnowledgeAdminController(null,null,null,null,null);
        ReflectionTestUtils.setField(controller,"prepareObservation",service);
        var users=mock(SysUserService.class);var aspect=new AuthInterceptor();
        ReflectionTestUtils.setField(aspect,"sysUserService",users);
        var factory=new AspectJProxyFactory(controller);factory.addAspect(aspect);
        CustomerServiceKnowledgeAdminController proxy=factory.getProxy();
        var request=new MockHttpServletRequest();RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
        try {
            when(users.getLoginUserVo(request)).thenThrow(new BusinessException(ErrorCode.NOT_LOGIN_ERROR));
            assertEquals(ErrorCode.NOT_LOGIN_ERROR.getCode(),assertThrows(BusinessException.class,()->proxy.prepareExecution(2,1)).getCode());
            var user=new SysUserVO();user.setUserRole("user");doReturn(user).when(users).getLoginUserVo(request);
            assertEquals(ErrorCode.NO_AUTH_ERROR.getCode(),assertThrows(BusinessException.class,()->proxy.prepareExecution(2,1)).getCode());
            verifyNoInteractions(mapper);
            user.setUserRole("admin");var row=new KnowledgePrepareObservationRow();row.setTaskId(1);when(mapper.observePrepareExecution(2,1)).thenReturn(row);
            var result=proxy.prepareExecution(2,1);assertEquals(0,result.getCode());
            var tree=new ObjectMapper().findAndRegisterModules().valueToTree(result.getData());
            assertEquals(6,tree.size());assertFalse(tree.has("receiptJson"));assertFalse(tree.has("executorId"));
            assertEquals("2030-01-01T00:00:00Z",tree.get("observedAt").asText());
        } finally {RequestContextHolder.resetRequestAttributes();}
    }
}
