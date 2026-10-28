package com.yupi.yuaicodemother.service.impl;

import com.yupi.yuaicodemother.ai.gateway.*;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.artifact.HtmlOutputBudgetGuard;
import com.yupi.yuaicodemother.core.handler.StreamHandlerExecutor;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.GenerationTask;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.*;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;
import org.mockito.ArgumentCaptor;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Sinks;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AppGenerationTaskTest {
    private final AiGenerationGateway gateway = mock(AiGenerationGateway.class);
    private final GenerationLeaseService leases = mock(GenerationLeaseService.class);
    private final GenerationTaskService tasks = mock(GenerationTaskService.class);
    private final ChatHistoryService history = mock(ChatHistoryService.class);
    private final ChatHistoryOriginalService originals = mock(ChatHistoryOriginalService.class);
    private final StreamHandlerExecutor streams = mock(StreamHandlerExecutor.class);
    private final ArtifactPathResolver artifacts = mock(ArtifactPathResolver.class);
    private final GenerationLease lease = mock(GenerationLease.class);
    private final SysUser owner = SysUser.builder().id(7L).build();

    private AppServiceImpl fixture(CodeGenTypeEnum type) {
        var service = spy(new AppServiceImpl(mock(SysUserService.class), gateway, history, artifacts, leases,
                mock(HtmlOutputBudgetGuard.class)));
        ReflectionTestUtils.setField(service, "generationTaskService", tasks);
        ReflectionTestUtils.setField(service, "chatHistoryOriginalService", originals);
        ReflectionTestUtils.setField(service, "streamHandlerExecutor", streams);
        doReturn(App.builder().id(42L).userId(7L).codeGenType(type.getValue()).build()).when(service).getById(42L);
        when(leases.acquire(eq(42L), anyString())).thenReturn(lease);
        when(lease.fenceToken()).thenReturn(99L);
        when(gateway.generate(anyString(), eq(type), eq(42L), eq(7L), anyString())).thenReturn(Flux.just("text"));
        when(streams.doExecute(any(), eq(history), eq(originals), eq(42L), eq(owner), eq(type)))
                .thenAnswer(call -> call.getArgument(0));
        return service;
    }

    @ParameterizedTest
    @EnumSource(CodeGenTypeEnum.class)
    void allTypesPersistBeforeModelAndRecordSuccessAndNewRequestForResubscription(CodeGenTypeEnum type) {
        var service = fixture(type);
        var stream = service.chatToGenCode(42L, "build", owner);
        assertEquals("text", stream.blockLast());
        assertEquals("text", stream.blockLast());
        ArgumentCaptor<String> ids = ArgumentCaptor.forClass(String.class);
        verify(tasks, times(2)).start(eq(42L), eq(7L), ids.capture(), eq(type), eq(99L));
        assertNotEquals(ids.getAllValues().get(0), ids.getAllValues().get(1));
        var order = inOrder(tasks, history, gateway);
        for (String id : ids.getAllValues()) {
            order.verify(tasks).start(42L, 7L, id, type, 99L);
            order.verify(history).addChatMessage(eq(42L), eq("build"), anyString(), eq(7L));
            order.verify(gateway).generate("build", type, 42L, 7L, id);
            order.verify(tasks).finish(id, "SUCCEEDED", null);
        }
        verify(leases, times(2)).release(lease);
    }

    @Test
    void ledgerInsertFailureOrDuplicateStopsBeforeHistoryAndModelAndReleasesLease() {
        var service = fixture(CodeGenTypeEnum.HTML);
        doThrow(new DuplicateKeyException("duplicate")).when(tasks).start(anyLong(), anyLong(), anyString(), any(), anyLong());
        assertThrows(GenerationStreamException.class, () -> service.chatToGenCode(42L, "build", owner).blockLast());
        verifyNoInteractions(history, originals);
        verify(gateway, never()).generate(anyString(), any(), anyLong(), anyLong(), anyString());
        verify(tasks, never()).finish(anyString(), anyString(), any());
        verify(leases).release(lease);
    }

    @Test
    void durableCancellationBeforeTaskInsertStopsModelEvenWhenRedisMirrorShowsRunning() {
        var service = fixture(CodeGenTypeEnum.HTML);
        when(leases.isCancellationConfirmed(eq(42L), anyString())).thenReturn(true);
        when(leases.getStatus(42L)).thenAnswer(call ->
                new GenerationLeaseService.GenerationStateSnapshot("stale-mirror",
                        GenerationLeaseService.PublicState.RUNNING, false));
        var error = assertThrows(GenerationStreamException.class,
                () -> service.chatToGenCode(42L, "build", owner).blockLast());
        assertEquals("GENERATION_CANCELLED", error.getErrorCode());
        verifyNoInteractions(history, originals);
        verify(gateway, never()).generate(anyString(), any(), anyLong(), anyLong(), anyString());
        verify(tasks).finish(error.getRequestId(), "CANCELLED", "GENERATION_CANCELLED");
        verify(leases).release(lease);
    }

    @Test
    void modelErrorPersistsStableFailureAndCannotReportSuccess() {
        var service = fixture(CodeGenTypeEnum.MULTI_FILE);
        when(gateway.generate(anyString(), any(), anyLong(), anyLong(), anyString()))
                .thenReturn(Flux.error(new GenerationStreamException(50001, "MODEL_OUTPUT_TRUNCATED", "", "截断", null)));
        assertThrows(GenerationStreamException.class, () -> service.chatToGenCode(42L, "build", owner).blockLast());
        verify(tasks).finish(anyString(), eq("FAILED"), eq("MODEL_OUTPUT_TRUNCATED"));
        verify(tasks, never()).finish(anyString(), eq("SUCCEEDED"), any());
        verify(leases).release(lease);
    }

    @Test
    void disconnectRecordsWinningCancelButKeepsLeaseUntilEngineTerminal() {
        var service = fixture(CodeGenTypeEnum.VUE_PROJECT);
        var engine = Sinks.many().unicast().<String>onBackpressureBuffer();
        when(gateway.generate(anyString(), any(), anyLong(), anyLong(), anyString())).thenReturn(engine.asFlux());
        when(leases.cancel(lease)).thenReturn(true);
        var subscriber = service.chatToGenCode(42L, "build", owner).subscribe(value -> {}, error -> {});
        subscriber.dispose();
        ArgumentCaptor<String> id = ArgumentCaptor.forClass(String.class);
        verify(tasks).recordCancellation(id.capture());
        verify(gateway).cancel(42L, 7L, id.getValue());
        verify(leases, never()).release(lease);
        engine.tryEmitError(new GenerationStreamException(50001, "cancelled", id.getValue(), "已取消", null));
        verify(tasks).finish(id.getValue(), "CANCELLED", "cancelled");
        verify(leases).release(lease);
    }

    @Test
    void cancelBeforeTaskInsertIsBackfilledAndCommitWinningCancelHasNoTombstone() {
        var service = fixture(CodeGenTypeEnum.HTML);
        doAnswer(call -> {
            when(leases.getStatus(42L)).thenReturn(new GenerationLeaseService.GenerationStateSnapshot(
                    call.getArgument(2), GenerationLeaseService.PublicState.STOPPING, false));
            return null;
        }).when(tasks).start(anyLong(), anyLong(), anyString(), any(), anyLong());
        assertThrows(GenerationStreamException.class, () -> service.chatToGenCode(42L, "build", owner).blockLast());
        verify(tasks).recordCancellation(anyString());
        verify(tasks).finish(anyString(), eq("CANCELLED"), eq("GENERATION_CANCELLED"));
        verifyNoInteractions(history);
        verify(gateway, never()).generate(anyString(), any(), anyLong(), anyLong(), anyString());
        clearInvocations(tasks);
        when(leases.cancelActive(42L)).thenReturn(new GenerationLeaseService.GenerationStateSnapshot(
                "req", GenerationLeaseService.PublicState.COMMITTING, false));
        service.cancelGeneration(42L, owner);
        verifyNoInteractions(tasks);
    }

    @Test
    void explicitCancellationPersistsOnlyWinningRequestAndRepeatedCancelIsIdempotent() {
        var service = fixture(CodeGenTypeEnum.MULTI_FILE);
        when(leases.cancelActive(42L)).thenReturn(new GenerationLeaseService.GenerationStateSnapshot(
                "authoritative", GenerationLeaseService.PublicState.STOPPING, true));
        service.cancelGeneration(42L, owner);
        var order = inOrder(leases, tasks, gateway);
        order.verify(leases).cancelActive(42L);
        order.verify(tasks).recordCancellation("authoritative");
        order.verify(gateway).cancel(42L, 7L, "authoritative");
        when(leases.cancelActive(42L)).thenReturn(new GenerationLeaseService.GenerationStateSnapshot(
                "authoritative", GenerationLeaseService.PublicState.STOPPING, false));
        service.cancelGeneration(42L, owner);
        verify(tasks, times(1)).recordCancellation("authoritative");
    }

    @Test
    void ownedQueryKeepsExecutionSeparateFromUnmatchedLeaseAndCommittedPublication() {
        var service = fixture(CodeGenTypeEnum.HTML);
        var task = new GenerationTask();
        task.setRequestId("req-old");
        task.setAppId(42L);
        task.setUserId(7L);
        task.setCodeGenType("html");
        task.setExecutionState("FAILED");
        when(tasks.findOwned(42L, 7L, "req-old")).thenReturn(task);
        when(leases.getStatus(42L)).thenReturn(new GenerationLeaseService.GenerationStateSnapshot(
                "new", GenerationLeaseService.PublicState.RUNNING, false));
        when(artifacts.publicationState(CodeGenTypeEnum.HTML, 42L, "req-old")).thenReturn("COMMITTED");
        var result = service.getGenerationTask(42L, "req-old", owner);
        assertEquals("FAILED", result.getExecutionState());
        assertEquals("UNKNOWN", result.getLeaseState());
        assertEquals("COMMITTED", result.getPublicationState());
        assertThrows(BusinessException.class, () -> service.getGenerationTask(42L, "req-old", SysUser.builder().id(8L).build()));
        assertThrows(BusinessException.class, () -> service.getGenerationTask(42L, "req-old", null));
        verify(tasks, times(1)).findOwned(anyLong(), anyLong(), any());
        when(leases.getStatus(42L)).thenThrow(new IllegalStateException("Redis 不可用"));
        assertEquals("UNKNOWN", service.getGenerationTask(42L, "req-old", owner).getLeaseState());
        doReturn(null).when(service).getById(42L);
        BusinessException absent = assertThrows(BusinessException.class, () -> service.getGenerationTask(42L, "req-old", owner));
        assertEquals(ErrorCode.NOT_FOUND_ERROR.getCode(), absent.getCode());
    }
}
