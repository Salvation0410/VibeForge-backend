package com.yupi.yuaicodemother.service.impl;

import com.yupi.yuaicodemother.ai.gateway.AiGenerationGateway;
import com.yupi.yuaicodemother.ai.gateway.GenerationLease;
import com.yupi.yuaicodemother.ai.gateway.GenerationLeaseService;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.handler.StreamHandlerExecutor;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.ChatHistoryOriginalService;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import com.yupi.yuaicodemother.service.SysUserService;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Sinks;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class AppServiceGenerationCancellationTest {

    @Test
    void downstreamCancelKeepsLeaseUntilEngineTerminates() {
        AiGenerationGateway gateway = mock(AiGenerationGateway.class);
        ChatHistoryService history = mock(ChatHistoryService.class);
        ChatHistoryOriginalService originalHistory = mock(ChatHistoryOriginalService.class);
        GenerationLeaseService leases = mock(GenerationLeaseService.class);
        GenerationLease lease = mock(GenerationLease.class);
        StreamHandlerExecutor streamHandler = mock(StreamHandlerExecutor.class);
        Sinks.Many<String> engine = Sinks.many().unicast().onBackpressureBuffer();

        when(leases.acquire(eq(42L), anyString())).thenReturn(lease);
        when(leases.cancel(lease)).thenReturn(true);
        when(gateway.generate(anyString(), eq(CodeGenTypeEnum.MULTI_FILE), eq(42L), eq(7L), anyString()))
                .thenReturn(engine.asFlux());
        when(streamHandler.doExecute(any(), eq(history), eq(originalHistory), eq(42L), any(),
                eq(CodeGenTypeEnum.MULTI_FILE))).thenAnswer(invocation -> invocation.getArgument(0));

        AppServiceImpl service = spy(new AppServiceImpl(mock(SysUserService.class), gateway, history,
                mock(ArtifactPathResolver.class), leases));
        ReflectionTestUtils.setField(service, "streamHandlerExecutor", streamHandler);
        ReflectionTestUtils.setField(service, "chatHistoryOriginalService", originalHistory);
        doReturn(App.builder().id(42L).userId(7L).codeGenType("multi_file").build())
                .when(service).getById(42L);

        var subscription = service.chatToGenCode(42L, "build", SysUser.builder().id(7L).build()).subscribe();
        subscription.dispose();

        verify(gateway, timeout(1000)).cancel(eq(42L), eq(7L), anyString());
        verify(leases, never()).release(lease);

        engine.tryEmitError(new IllegalStateException("cancelled"));
        verify(leases, timeout(1000)).release(lease);
    }
}
