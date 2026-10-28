package com.yupi.yuaicodemother.service.impl;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.ai.gateway.GenerationStreamException;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.mapper.GenerationTaskMapper;
import com.yupi.yuaicodemother.model.entity.GenerationTask;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class GenerationTaskServiceTest {
    @Test
    void startFailsClosedAndOnlyCapturesIdentityAndType() {
        var mapper = mock(GenerationTaskMapper.class);
        var service = new GenerationTaskServiceImpl(mapper);
        assertThrows(GenerationStreamException.class, () -> service.start(42, 7, "req", CodeGenTypeEnum.HTML, 11));
        when(mapper.createTask(any())).thenReturn(1);
        service.start(42, 7, "req", CodeGenTypeEnum.HTML, 11);
        ArgumentCaptor<GenerationTask> task = ArgumentCaptor.forClass(GenerationTask.class);
        verify(mapper, times(2)).createTask(task.capture());
        assertEquals(42L, task.getValue().getAppId());
        assertEquals(7L, task.getValue().getUserId());
        assertEquals("html", task.getValue().getCodeGenType());
        assertEquals(11L, task.getValue().getFenceToken());
    }

    @Test
    void startDoesNotExposeJdbcDetailsAndDistinguishesDuplicateRequests() {
        var mapper = mock(GenerationTaskMapper.class);
        var service = new GenerationTaskServiceImpl(mapper);
        when(mapper.createTask(any())).thenThrow(new IllegalStateException("sensitive SQL connection details"));
        var unavailable = assertThrows(GenerationStreamException.class, () -> service.start(42, 7, "req", CodeGenTypeEnum.HTML, 11));
        assertEquals("GENERATION_TASK_UNAVAILABLE", unavailable.getErrorCode());
        assertFalse(unavailable.getMessage().contains("sensitive"));
        doThrow(new org.springframework.dao.DuplicateKeyException("duplicate")).when(mapper).createTask(any());
        var duplicate = assertThrows(GenerationStreamException.class, () -> service.start(42, 7, "req", CodeGenTypeEnum.HTML, 11));
        assertEquals("GENERATION_REQUEST_EXISTS", duplicate.getErrorCode());
    }

    @Test
    void terminalPersistenceIsBestEffortAndUsesThirtyDayWindowAndStableCode() {
        var mapper = mock(GenerationTaskMapper.class);
        var service = new GenerationTaskServiceImpl(mapper);
        when(mapper.createTask(any())).thenReturn(1);
        service.start(42, 7, "req", CodeGenTypeEnum.HTML, 11);
        when(mapper.finishTask(anyString(), anyString(), any(), any(), any(), anyString(), anyLong())).thenThrow(new IllegalStateException("数据库不可用"));
        assertDoesNotThrow(() -> service.finish("req", "FAILED", "含敏感异常内容"));
        ArgumentCaptor<LocalDateTime> now = ArgumentCaptor.forClass(LocalDateTime.class);
        ArgumentCaptor<LocalDateTime> until = ArgumentCaptor.forClass(LocalDateTime.class);
        verify(mapper).finishTask(eq("req"), eq("FAILED"), eq("GENERATION_FAILED"), now.capture(), until.capture(), anyString(), eq(11L));
        assertEquals(now.getValue().plusDays(30), until.getValue());
        assertThrows(IllegalArgumentException.class, () -> service.finish("req", "RUNNING", null));
        doThrow(new IllegalStateException()).when(mapper).recordCancellation(anyString(), any());
        assertDoesNotThrow(() -> service.recordCancellation("req"));
    }

    @Test
    void queriesValidateIdsAndUseOwnerScope() {
        var mapper = mock(GenerationTaskMapper.class);
        var service = new GenerationTaskServiceImpl(mapper);
        assertThrows(BusinessException.class, () -> service.findOwned(42, 7, "../bad"));
        verifyNoInteractions(mapper);
        assertThrows(BusinessException.class, () -> service.findOwned(42, 7, "req"));
        verify(mapper).findOwned(eq(42L), eq(7L), eq("req"), any());
    }

    @Test
    void heartbeatsOnlyConfirmedOwnedExecutionsAndStopsAfterTerminalEvenWhenPersistenceFails() {
        var mapper = mock(GenerationTaskMapper.class);
        var service = new GenerationTaskServiceImpl(mapper);
        when(mapper.createTask(any())).thenReturn(1);
        service.start(42, 7, "owned", CodeGenTypeEnum.HTML, 11);
        ArgumentCaptor<GenerationTask> inserted = ArgumentCaptor.forClass(GenerationTask.class);
        verify(mapper).createTask(inserted.capture());
        String owner = inserted.getValue().getExecutionOwner();
        assertNotNull(java.util.UUID.fromString(owner));
        when(mapper.heartbeat("owned", 42, owner, 11)).thenReturn(1);
        service.heartbeatOwnedExecutions();
        verify(mapper).heartbeat("owned", 42, owner, 11);
        doThrow(new IllegalStateException("模拟落账失败")).when(mapper)
                .finishTask(eq("owned"), anyString(), any(), any(), any(), eq(owner), eq(11L));
        service.finish("owned", "SUCCEEDED", null);
        service.heartbeatOwnedExecutions();
        verify(mapper, times(1)).heartbeat("owned", 42, owner, 11);
        service.finish("owned", "FAILED", "late");
        verify(mapper, times(1)).finishTask(eq("owned"), anyString(), any(), any(), any(), eq(owner), eq(11L));
    }

    @Test
    void heartbeatRetriesTransientFailureButNeverRetriesConditionalLossOrRebuildsOldOwnership() {
        var mapper = mock(GenerationTaskMapper.class);
        var service = new GenerationTaskServiceImpl(mapper);
        when(mapper.createTask(any())).thenReturn(1);
        service.start(42, 7, "owned", CodeGenTypeEnum.HTML, 11);
        when(mapper.heartbeat(eq("owned"), eq(42L), anyString(), eq(11L)))
                .thenThrow(new IllegalStateException("模拟瞬时数据库失败")).thenReturn(1).thenReturn(0);
        for (int count = 0; count < 4; count++) service.heartbeatOwnedExecutions();
        verify(mapper, times(3)).heartbeat(eq("owned"), eq(42L), anyString(), eq(11L));
        var rebuilt = new GenerationTaskServiceImpl(mapper);
        rebuilt.heartbeatOwnedExecutions();
        rebuilt.finish("owned", "FAILED", "stale");
        verify(mapper, never()).finishTask(anyString(), anyString(), any(), any(), any(), anyString(), anyLong());
        service.clearOwnedExecutions();
        service.heartbeatOwnedExecutions();
        verify(mapper, times(3)).heartbeat(eq("owned"), eq(42L), anyString(), eq(11L));
    }

    @Test
    void unconfirmedInsertDoesNotRegisterHeartbeatOrAllowTerminalCallback() {
        var mapper = mock(GenerationTaskMapper.class);
        var service = new GenerationTaskServiceImpl(mapper);
        when(mapper.createTask(any())).thenThrow(new IllegalStateException("模拟 INSERT 确认丢失"));
        assertThrows(GenerationStreamException.class, () -> service.start(42, 7, "uncertain", CodeGenTypeEnum.HTML, 11));
        service.heartbeatOwnedExecutions();
        service.finish("uncertain", "FAILED", "uncertain");
        verify(mapper, never()).heartbeat(anyString(), anyLong(), anyString(), anyLong());
        verify(mapper, never()).finishTask(anyString(), anyString(), any(), any(), any(), anyString(), anyLong());
    }
}
