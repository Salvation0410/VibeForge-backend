package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeEtlOutboxMapper;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeEtlOutbox;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import com.yupi.yuaicodemother.worker.CustomerServiceKnowledgeEtlWorker;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;

import java.net.URL;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class CustomerServiceKnowledgeEtlWorkerTest {
    CustomerServiceKnowledgeDocumentMapper documents = mock(CustomerServiceKnowledgeDocumentMapper.class);
    CustomerServiceKnowledgeEtlOutboxMapper outbox = mock(CustomerServiceKnowledgeEtlOutboxMapper.class);
    OssManager oss = mock(OssManager.class);
    CustomerServiceAiClient ai = mock(CustomerServiceAiClient.class);
    KnowledgeMutationCoordinator coordinator = mock(KnowledgeMutationCoordinator.class);
    CustomerServiceProperties props = new CustomerServiceProperties();
    CustomerServiceKnowledgeEtlWorker worker;

    @BeforeEach
    void setUp() {
        props.setRetryMax(5);
        when(outbox.refreshClaim(anyLong(), anyString(), any())).thenReturn(1);
        worker = new CustomerServiceKnowledgeEtlWorker(documents, outbox, oss, ai, coordinator, props,
                Clock.fixed(Instant.parse("2030-01-01T00:00:00Z"), ZoneOffset.UTC));
    }

    @Test
    void lostClaimDoesNotExecuteTask() {
        when(outbox.findClaimCandidates(any(), anyInt())).thenReturn(List.of(task("INDEX")));
        when(outbox.claim(anyLong(), anyString(), any(), any())).thenReturn(0);
        worker.poll();
        verifyNoInteractions(coordinator, ai);
    }

    @Test
    void eachCandidateReceivesFullClaimTtlAfterPreviousTaskRunsSlowly() {
        MutableClock clock = new MutableClock(Instant.parse("2030-01-01T00:00:00Z"));
        var first = task("INDEX");
        var second = task("INDEX"); second.setId(2L);
        when(outbox.findClaimCandidates(any(), anyInt())).thenReturn(List.of(first, second));
        when(outbox.claim(anyLong(), anyString(), any(), any())).thenReturn(1);
        var pollingWorker = spy(new CustomerServiceKnowledgeEtlWorker(
                documents, outbox, oss, ai, coordinator, props, clock));
        doAnswer(invocation -> {
            if (((CustomerServiceKnowledgeEtlOutbox) invocation.getArgument(0)).getId() == 1L) {
                clock.advanceSeconds(45);
            }
            return null;
        }).when(pollingWorker).execute(any());

        pollingWorker.poll();

        var claimNow = org.mockito.ArgumentCaptor.forClass(LocalDateTime.class);
        var deadline = org.mockito.ArgumentCaptor.forClass(LocalDateTime.class);
        verify(outbox, times(2)).claim(anyLong(), anyString(), claimNow.capture(), deadline.capture());
        assertEquals(List.of(
                LocalDateTime.parse("2030-01-01T00:00:00"),
                LocalDateTime.parse("2030-01-01T00:00:45")), claimNow.getAllValues());
        assertEquals(List.of(
                LocalDateTime.parse("2030-01-01T00:01:00"),
                LocalDateTime.parse("2030-01-01T00:01:45")), deadline.getAllValues());
    }

    @Test
    void staleVersionIsSkippedWithoutCallingPython() {
        var task = task("INDEX");
        var lease = lease("INDEX");
        when(coordinator.acquire(any(), any(), any(), any())).thenReturn(lease);
        var newer = document();
        newer.setDocumentVersion(2L);
        when(documents.findIncludingDeleted(1)).thenReturn(newer);
        worker.execute(task);
        verify(outbox).finish(eq(1L), anyString(), eq("SKIPPED"), eq("KNOWLEDGE_TASK_STALE"));
        verifyNoInteractions(ai);
        verifyNoInteractions(coordinator);
    }

    @Test
    void deterministicFailureDoesNotRetry() throws Exception {
        var task = task("INDEX");
        when(coordinator.acquire(any(), any(), any(), any())).thenReturn(lease("INDEX"));
        when(documents.findIncludingDeleted(1)).thenReturn(document());
        when(documents.markIndexing(1, 1, 1)).thenReturn(1);
        when(oss.generateKnowledgeDownloadUrl(any())).thenReturn(new URL("https://example.com/short"));
        when(ai.index(any())).thenThrow(new CustomerServiceAiClient.CallException("BAD_FILE", false));
        worker.execute(task);
        verify(outbox).retry(eq(1L), anyString(), eq("FAILED"), eq(1), any(), eq("BAD_FILE"));
        verify(documents).failIndex(1, 1, 1, "BAD_FILE");
    }

    @Test
    void fifthTransientFailureStopsRetrying() throws Exception {
        var task = task("INDEX");
        task.setRetryCount(4);
        when(coordinator.acquire(any(), any(), any(), any())).thenReturn(lease("INDEX"));
        when(documents.findIncludingDeleted(1)).thenReturn(document());
        when(documents.markIndexing(1, 1, 1)).thenReturn(1);
        when(oss.generateKnowledgeDownloadUrl(any())).thenReturn(new URL("https://example.com/short"));
        when(ai.index(any())).thenThrow(new CustomerServiceAiClient.CallException("TEMPORARY", true));
        worker.execute(task);
        verify(outbox).retry(eq(1L), anyString(), eq("FAILED"), eq(5), any(), eq("TEMPORARY"));
    }

    @Test
    void transientIndexFailureCanReenterIndexingAndThenSucceed() throws Exception {
        var task = task("INDEX");
        var lease = lease("INDEX");
        when(coordinator.acquire(any(), any(), any(), any())).thenReturn(lease);
        var document = document(); document.setStatus("INDEXING");
        when(documents.findIncludingDeleted(1)).thenReturn(document);
        when(documents.markIndexing(1, 1, 1)).thenReturn(1);
        when(oss.generateKnowledgeDownloadUrl(any())).thenReturn(new URL("https://example.com/short"));
        when(ai.index(any())).thenThrow(new CustomerServiceAiClient.CallException("TEMPORARY", true))
                .thenReturn(new CustomerServiceAiClient.Result(2, false));
        when(documents.completeIndex(1, 1, 1, 2)).thenReturn(1);

        worker.execute(task);
        task.setRetryCount(1);
        worker.execute(task);

        verify(ai, times(2)).index(any());
        verify(documents).completeIndex(1, 1, 1, 2);
        verify(outbox).finish(eq(1L), anyString(), eq("SUCCEEDED"), isNull());
    }

    @Test
    void expiredProcessingClaimCanReenterIndexing() throws Exception {
        var task = task("INDEX"); task.setStatus("PROCESSING");
        var document = document(); document.setStatus("INDEXING");
        when(outbox.findClaimCandidates(any(), anyInt())).thenReturn(List.of(task));
        when(outbox.claim(anyLong(), anyString(), any(), any())).thenReturn(1);
        when(coordinator.acquire(any(), any(), any(), any())).thenReturn(lease("INDEX"));
        when(documents.findIncludingDeleted(1)).thenReturn(document);
        when(documents.markIndexing(1, 1, 1)).thenReturn(1);
        when(oss.generateKnowledgeDownloadUrl(any())).thenReturn(new URL("https://example.com/short"));
        when(ai.index(any())).thenReturn(new CustomerServiceAiClient.Result(1, false));
        when(documents.completeIndex(1, 1, 1, 1)).thenReturn(1);
        worker.poll();
        verify(ai).index(any());
        verify(outbox).finish(eq(1L), anyString(), eq("SUCCEEDED"), isNull());
    }

    @Test
    void workerIsFeatureConditional() {
        ConditionalOnProperty condition = CustomerServiceKnowledgeEtlWorker.class.getAnnotation(ConditionalOnProperty.class);
        org.junit.jupiter.api.Assertions.assertNotNull(condition);
        org.junit.jupiter.api.Assertions.assertArrayEquals(new String[]{"enabled"}, condition.name());
        org.junit.jupiter.api.Assertions.assertEquals("true", condition.havingValue());
    }

    @Test
    void rebuildHoldsCollectionLeaseForPythonCall() {
        var task = task("REBUILD");
        task.setDocumentId(0L); task.setDocumentVersion(0L);
        var lease = new KnowledgeMutationCoordinator.Lease("collection:customer_service_knowledge", "op_rebuild", "REBUILD", 8, 2000000000, "proof");
        when(coordinator.acquire(eq("collection:customer_service_knowledge"), any(), eq("REBUILD"), any())).thenReturn(lease);
        when(documents.listAllActive()).thenReturn(List.of());
        when(ai.rebuild(any())).thenReturn(new CustomerServiceAiClient.RebuildResult(0, false));
        worker.execute(task);
        var order = inOrder(documents, outbox, coordinator, ai);
        order.verify(documents).listAllActive();
        order.verify(outbox).refreshClaim(eq(1L), anyString(), eq(LocalDateTime.parse("2030-01-01T00:01:00")));
        order.verify(coordinator).acquire(eq("collection:customer_service_knowledge"), any(), eq("REBUILD"), any());
        order.verify(ai).rebuild(argThat(request -> request.lease() == lease));
        order.verify(outbox).finish(eq(1L), anyString(), eq("SUCCEEDED"), isNull());
        order.verify(coordinator).revoke(lease);
    }

    @Test
    void rebuildStopsWhenPreparationOutlivesClaimAndAnotherOwnerReclaimsIt() {
        MutableClock clock = new MutableClock(Instant.parse("2030-01-01T00:00:00Z"));
        worker = new CustomerServiceKnowledgeEtlWorker(documents, outbox, oss, ai, coordinator, props, clock);
        when(documents.listAllActive()).thenAnswer(invocation -> {
            clock.advanceSeconds(61);
            return List.of();
        });
        when(outbox.refreshClaim(anyLong(), anyString(), any())).thenReturn(0);

        worker.execute(task("REBUILD"));

        verify(outbox).refreshClaim(eq(1L), anyString(), eq(LocalDateTime.parse("2030-01-01T00:02:01")));
        verifyNoInteractions(coordinator, ai);
        verify(outbox, never()).finish(anyLong(), anyString(), anyString(), any());
        verify(outbox, never()).retry(anyLong(), anyString(), anyString(), anyInt(), any(), anyString());
    }

    @Test
    void rebuildContinuesAfterSlowPreparationWhenClaimRefreshStillOwnsTask() {
        MutableClock clock = new MutableClock(Instant.parse("2030-01-01T00:00:00Z"));
        worker = new CustomerServiceKnowledgeEtlWorker(documents, outbox, oss, ai, coordinator, props, clock);
        var lease = new KnowledgeMutationCoordinator.Lease(
                "collection:customer_service_knowledge", "op_rebuild", "REBUILD", 8, 2000000000, "proof");
        when(documents.listAllActive()).thenAnswer(invocation -> {
            clock.advanceSeconds(61);
            return List.of();
        });
        when(outbox.refreshClaim(anyLong(), anyString(), any())).thenReturn(1);
        when(coordinator.acquire(any(), any(), any(), any())).thenReturn(lease);
        when(ai.rebuild(any())).thenReturn(new CustomerServiceAiClient.RebuildResult(0, false));

        worker.execute(task("REBUILD"));

        var order = inOrder(documents, outbox, coordinator, ai);
        order.verify(documents).listAllActive();
        order.verify(outbox).refreshClaim(eq(1L), anyString(), eq(LocalDateTime.parse("2030-01-01T00:02:01")));
        order.verify(coordinator).acquire(eq("collection:customer_service_knowledge"), any(), eq("REBUILD"), any());
        order.verify(ai).rebuild(any());
        order.verify(outbox).finish(eq(1L), anyString(), eq("SUCCEEDED"), isNull());
    }

    private static CustomerServiceKnowledgeEtlOutbox task(String operation) {
        var task = new CustomerServiceKnowledgeEtlOutbox();
        task.setId(1L); task.setDocumentId(1L); task.setDocumentVersion(1L); task.setEtlVersion(1L);
        task.setOperation(operation); task.setRetryCount(0);
        return task;
    }
    private static CustomerServiceKnowledgeDocument document() {
        var doc = new CustomerServiceKnowledgeDocument();
        doc.setId(1L); doc.setDocumentVersion(1L); doc.setEtlVersion(1L); doc.setIsDelete(0);
        doc.setName("a.txt"); doc.setFileType("TXT"); doc.setObjectKey("key"); doc.setContentHash("a".repeat(64));
        return doc;
    }
    private static KnowledgeMutationCoordinator.Lease lease(String operation) {
        return new KnowledgeMutationCoordinator.Lease("document:1", "op_1", operation, 1, 2000000000, "proof");
    }

    private static final class MutableClock extends Clock {
        private Instant instant;

        private MutableClock(Instant instant) { this.instant = instant; }

        private void advanceSeconds(long seconds) { instant = instant.plusSeconds(seconds); }

        @Override
        public ZoneId getZone() { return ZoneOffset.UTC; }

        @Override
        public Clock withZone(ZoneId zone) { return this; }

        @Override
        public Instant instant() { return instant; }
    }
}
