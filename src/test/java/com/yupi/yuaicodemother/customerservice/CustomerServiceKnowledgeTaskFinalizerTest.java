package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeEtlOutboxMapper;
import com.yupi.yuaicodemother.service.CustomerServiceKnowledgeTaskFinalizer;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.*;

class CustomerServiceKnowledgeTaskFinalizerTest {
    private final CustomerServiceKnowledgeDocumentMapper documents = mock(CustomerServiceKnowledgeDocumentMapper.class);
    private final CustomerServiceKnowledgeEtlOutboxMapper outbox = mock(CustomerServiceKnowledgeEtlOutboxMapper.class);
    private CustomerServiceKnowledgeTaskFinalizer finalizer;

    @BeforeEach
    void setUp() {
        finalizer = new CustomerServiceKnowledgeTaskFinalizer(documents, outbox);
    }

    @Test
    void indexSuccessUpdatesDocumentAndOutbox() {
        when(documents.completeIndex(3, 4, 5, 6)).thenReturn(1);
        when(outbox.finish(1, "owner", "SUCCEEDED", null)).thenReturn(1);

        finalizer.completeIndex(1, "owner", 3, 4, 5, 6);

        var order = inOrder(documents, outbox);
        order.verify(documents).completeIndex(3, 4, 5, 6);
        order.verify(outbox).finish(1, "owner", "SUCCEEDED", null);
    }

    @Test
    void indexSuccessThrowsWhenOutboxCasFails() {
        when(documents.completeIndex(3, 4, 5, 6)).thenReturn(1);
        when(outbox.finish(1, "owner", "SUCCEEDED", null)).thenReturn(0);

        var error = assertThrows(IllegalStateException.class,
                () -> finalizer.completeIndex(1, "owner", 3, 4, 5, 6));

        assertEquals("KNOWLEDGE_TASK_FINALIZATION_CONFLICT", error.getMessage());
    }

    @Test
    void staleIndexSuccessAtomicallySkipsOutbox() {
        when(documents.completeIndex(3, 4, 5, 6)).thenReturn(0);
        when(outbox.finish(1, "owner", "SKIPPED", "KNOWLEDGE_TASK_STALE")).thenReturn(1);

        finalizer.completeIndex(1, "owner", 3, 4, 5, 6);

        verify(outbox).finish(1, "owner", "SKIPPED", "KNOWLEDGE_TASK_STALE");
        verify(outbox, never()).finish(1, "owner", "SUCCEEDED", null);
    }

    @Test
    void terminalIndexFailureUpdatesDocumentAndOutbox() {
        LocalDateTime failedAt = LocalDateTime.parse("2030-01-01T00:00:00");
        when(documents.failIndex(3, 4, 5, "BAD_FILE")).thenReturn(1);
        when(outbox.retry(1, "owner", "FAILED", 2, failedAt, "BAD_FILE")).thenReturn(1);

        finalizer.failIndex(1, "owner", 3, 4, 5, 2, failedAt, "BAD_FILE");

        var order = inOrder(documents, outbox);
        order.verify(documents).failIndex(3, 4, 5, "BAD_FILE");
        order.verify(outbox).retry(1, "owner", "FAILED", 2, failedAt, "BAD_FILE");
    }

    @Test
    void terminalIndexFailureThrowsWhenOutboxWriteFails() {
        LocalDateTime failedAt = LocalDateTime.parse("2030-01-01T00:00:00");
        when(documents.failIndex(3, 4, 5, "BAD_FILE")).thenReturn(1);
        when(outbox.retry(1, "owner", "FAILED", 2, failedAt, "BAD_FILE"))
                .thenThrow(new IllegalStateException("database unavailable"));

        assertThrows(IllegalStateException.class,
                () -> finalizer.failIndex(1, "owner", 3, 4, 5, 2, failedAt, "BAD_FILE"));
    }

    @Test
    void staleTerminalFailureSkipsInsteadOfFailingOutbox() {
        LocalDateTime failedAt = LocalDateTime.parse("2030-01-01T00:00:00");
        when(documents.failIndex(3, 4, 5, "BAD_FILE")).thenReturn(0);
        when(outbox.finish(1, "owner", "SKIPPED", "KNOWLEDGE_TASK_STALE")).thenReturn(1);

        finalizer.failIndex(1, "owner", 3, 4, 5, 2, failedAt, "BAD_FILE");

        verify(outbox).finish(1, "owner", "SKIPPED", "KNOWLEDGE_TASK_STALE");
        verify(outbox, never()).retry(anyLong(), anyString(), eq("FAILED"), anyInt(), any(), anyString());
    }

    @Test
    void publicFinalizersAlwaysStartNewTransactions() throws Exception {
        for (String method : new String[]{"completeIndex", "failIndex"}) {
            Class<?>[] parameters = "completeIndex".equals(method)
                    ? new Class<?>[]{long.class, String.class, long.class, long.class, long.class, int.class}
                    : new Class<?>[]{long.class, String.class, long.class, long.class, long.class,
                    int.class, LocalDateTime.class, String.class};
            Transactional transactional = CustomerServiceKnowledgeTaskFinalizer.class
                    .getMethod(method, parameters).getAnnotation(Transactional.class);
            assertEquals(Propagation.REQUIRES_NEW, transactional.propagation());
        }
    }
}
