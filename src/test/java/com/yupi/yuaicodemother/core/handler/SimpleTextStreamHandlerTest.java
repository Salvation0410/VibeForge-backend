package com.yupi.yuaicodemother.core.handler;

import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import com.yupi.yuaicodemother.ai.gateway.StaticGenerationStreamFrame;
import org.junit.jupiter.api.Test;
import reactor.core.publisher.Flux;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class SimpleTextStreamHandlerTest {

    @Test
    void streamedDraftsAreDisplayedButOnlyCompleteCandidateIsSaved() {
        ChatHistoryService history = mock(ChatHistoryService.class);
        var chunks = new SimpleTextStreamHandler().handle(Flux.just(
                StaticGenerationStreamFrame.display("old draft"),
                StaticGenerationStreamFrame.display("new draft"),
                StaticGenerationStreamFrame.candidate("complete final")), history, 42,
                SysUser.builder().id(7L).build()).collectList().block();

        assertEquals(java.util.List.of("old draft", "new draft"), chunks);
        verify(history).addChatMessage(eq(42L), eq("complete final"), anyString(), eq(7L));
    }

    @Test
    void failedDraftDoesNotEnterHistory() {
        ChatHistoryService history = mock(ChatHistoryService.class);
        Flux<String> origin = Flux.concat(Flux.just(StaticGenerationStreamFrame.display("draft")),
                Flux.error(new IllegalStateException("truncated")));
        assertThrows(IllegalStateException.class, () -> new SimpleTextStreamHandler().handle(origin, history, 42,
                SysUser.builder().id(7L).build()).collectList().block());
        verifyNoInteractions(history);
    }

    @Test
    void missingFinalCandidateCannotSaveDisplayedText() {
        ChatHistoryService history = mock(ChatHistoryService.class);
        assertThrows(IllegalStateException.class, () -> new SimpleTextStreamHandler().handle(
                Flux.just(StaticGenerationStreamFrame.display("draft")), history, 42,
                SysUser.builder().id(7L).build()).collectList().block());
        verifyNoInteractions(history);
    }

    @Test
    void legacyResubscriptionsHaveIndependentHistoryBuffers() {
        ChatHistoryService history = mock(ChatHistoryService.class);
        var stream = new SimpleTextStreamHandler().handle(Flux.just("legacy"), history, 42,
                SysUser.builder().id(7L).build());
        stream.blockLast();
        stream.blockLast();
        verify(history, times(2)).addChatMessage(eq(42L), eq("legacy"), anyString(), eq(7L));
    }

    @Test
    void historyFailureDoesNotReversePublishedSuccess() {
        ChatHistoryService history = mock(ChatHistoryService.class);
        when(history.addChatMessage(anyLong(), anyString(), anyString(), anyLong()))
                .thenThrow(new IllegalStateException("database unavailable"));

        var chunks = new SimpleTextStreamHandler().handle(Flux.just("final-artifact"), history, 42,
                SysUser.builder().id(7L).build()).collectList().block();

        assertEquals(java.util.List.of("final-artifact"), chunks);
    }
}
