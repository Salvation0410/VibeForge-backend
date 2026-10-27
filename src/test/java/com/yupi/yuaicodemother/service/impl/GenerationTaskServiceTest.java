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
        assertThrows(GenerationStreamException.class, () -> service.start(42, 7, "req", CodeGenTypeEnum.HTML));
        when(mapper.createTask(any())).thenReturn(1);
        service.start(42, 7, "req", CodeGenTypeEnum.HTML);
        ArgumentCaptor<GenerationTask> task = ArgumentCaptor.forClass(GenerationTask.class);
        verify(mapper, times(2)).createTask(task.capture());
        assertEquals(42L, task.getValue().getAppId());
        assertEquals(7L, task.getValue().getUserId());
        assertEquals("html", task.getValue().getCodeGenType());
    }

    @Test
    void startDoesNotExposeJdbcDetailsAndDistinguishesDuplicateRequests() {
        var mapper = mock(GenerationTaskMapper.class);
        var service = new GenerationTaskServiceImpl(mapper);
        when(mapper.createTask(any())).thenThrow(new IllegalStateException("sensitive SQL connection details"));
        var unavailable = assertThrows(GenerationStreamException.class, () -> service.start(42, 7, "req", CodeGenTypeEnum.HTML));
        assertEquals("GENERATION_TASK_UNAVAILABLE", unavailable.getErrorCode());
        assertFalse(unavailable.getMessage().contains("sensitive"));
        doThrow(new org.springframework.dao.DuplicateKeyException("duplicate")).when(mapper).createTask(any());
        var duplicate = assertThrows(GenerationStreamException.class, () -> service.start(42, 7, "req", CodeGenTypeEnum.HTML));
        assertEquals("GENERATION_REQUEST_EXISTS", duplicate.getErrorCode());
    }

    @Test
    void terminalPersistenceIsBestEffortAndUsesThirtyDayWindowAndStableCode() {
        var mapper = mock(GenerationTaskMapper.class);
        var service = new GenerationTaskServiceImpl(mapper);
        when(mapper.finishTask(anyString(), anyString(), any(), any(), any())).thenThrow(new IllegalStateException("数据库不可用"));
        assertDoesNotThrow(() -> service.finish("req", "FAILED", "含敏感异常内容"));
        ArgumentCaptor<LocalDateTime> now = ArgumentCaptor.forClass(LocalDateTime.class);
        ArgumentCaptor<LocalDateTime> until = ArgumentCaptor.forClass(LocalDateTime.class);
        verify(mapper).finishTask(eq("req"), eq("FAILED"), eq("GENERATION_FAILED"), now.capture(), until.capture());
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
}
