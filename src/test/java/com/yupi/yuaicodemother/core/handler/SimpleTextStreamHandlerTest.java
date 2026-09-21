package com.yupi.yuaicodemother.core.handler;

import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import org.junit.jupiter.api.Test;
import reactor.core.publisher.Flux;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class SimpleTextStreamHandlerTest {

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
