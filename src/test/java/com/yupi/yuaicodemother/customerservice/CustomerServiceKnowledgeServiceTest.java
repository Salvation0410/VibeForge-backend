package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeEtlOutboxMapper;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeEtlOutbox;
import com.yupi.yuaicodemother.service.impl.CustomerServiceKnowledgeServiceImpl;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.TimeZone;
import org.mockito.ArgumentCaptor;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class CustomerServiceKnowledgeServiceTest {
    private CustomerServiceKnowledgeDocumentMapper documents;
    private CustomerServiceKnowledgeEtlOutboxMapper outbox;
    private OssManager oss;
    private CustomerServiceKnowledgeServiceImpl service;

    @BeforeEach
    void setUp() {
        documents = mock(CustomerServiceKnowledgeDocumentMapper.class);
        outbox = mock(CustomerServiceKnowledgeEtlOutboxMapper.class);
        oss = mock(OssManager.class);
        when(oss.uploadKnowledgeDocument(any())).thenReturn(new OssManager.KnowledgeObject(
                "customer-service-knowledge/0123456789abcdef0123456789abcdef.txt", "guide.txt", "txt", 4, "a".repeat(64)));
        when(documents.insert(any())).thenAnswer(call -> {
            call.getArgument(0, CustomerServiceKnowledgeDocument.class).setId(11L);
            return 1;
        });
        when(documents.insertContentRevision(any())).thenReturn(1);
        when(outbox.insert(any())).thenReturn(1);
        service = new CustomerServiceKnowledgeServiceImpl(documents, outbox, oss);
    }

    @Test
    void uploadCreatesDocumentAndOutbox() {
        var result = service.upload(new MockMultipartFile("file", "guide.txt", "text/plain", "data".getBytes()), null, 7);
        assertEquals(11, result.getId());
        assertEquals(1, result.getDocumentVersion());
        assertEquals(1, result.getActivationRevision());
        assertEquals(0, result.getIndexedEtlVersion());
        assertEquals(0, result.getIndexedActivationRevision());
        verify(documents).insertContentRevision(argThat(d -> d.getId() == 11L && d.getDocumentVersion() == 1L));
        assertNotNull(result.getCreateTime());
        assertNotNull(result.getUpdateTime());
        verify(documents).insert(any());
        verify(outbox).insert(argThat(task -> "INDEX".equals(task.getOperation())
                && task.getDocumentVersion() == 1
                && task.getCreateTime() != null
                && task.getUpdateTime() != null));
        verify(oss, never()).deleteKnowledgeObject(any());
    }

    @Test
    void firstTasksAreImmediatelyDueOnNonUtcServer() {
        TimeZone previous = TimeZone.getDefault();
        TimeZone.setDefault(TimeZone.getTimeZone("Asia/Shanghai"));
        try {
            LocalDateTime before = LocalDateTime.now(ZoneOffset.UTC);
            service.upload(new MockMultipartFile("file", "guide.txt", "text/plain", "data".getBytes()), null, 7);
            service.rebuild(7);
            LocalDateTime after = LocalDateTime.now(ZoneOffset.UTC);
            var tasks = ArgumentCaptor.forClass(CustomerServiceKnowledgeEtlOutbox.class);
            verify(outbox, times(2)).insert(tasks.capture());
            for (var task : tasks.getAllValues()) {
                assertFalse(task.getNextRetryTime().isBefore(before));
                assertFalse(task.getNextRetryTime().isAfter(after));
            }
        } finally {
            TimeZone.setDefault(previous);
        }
    }

    @Test
    void databaseFailureCompensatesUploadedObject() {
        when(outbox.insert(any())).thenReturn(0);
        assertThrows(IllegalStateException.class, () -> service.upload(
                new MockMultipartFile("file", "guide.txt", "text/plain", "data".getBytes()), null, 7));
        verify(oss).deleteKnowledgeObject("customer-service-knowledge/0123456789abcdef0123456789abcdef.txt");
    }

    @Test
    void duplicateCompensatesWithoutWritingDatabase() {
        when(documents.findDuplicate(any(), isNull())).thenReturn(new CustomerServiceKnowledgeDocument());
        assertThrows(RuntimeException.class, () -> service.upload(
                new MockMultipartFile("file", "guide.txt", "text/plain", "data".getBytes()), null, 7));
        verify(documents, never()).insert(any());
        verify(oss).deleteKnowledgeObject(any());
    }

    @Test
    void replacementIncrementsVersionAndPreservesIndexedVersion() {
        var old = document(9, 3, 2);
        when(documents.findIncludingDeletedForUpdate(9)).thenReturn(old);
        when(documents.update(any())).thenReturn(1);
        var result = service.upload(new MockMultipartFile("file", "guide.txt", "text/plain", "data".getBytes()), 9L, 7);
        assertEquals(4, result.getDocumentVersion());
        assertEquals(2, result.getIndexedVersion());
        assertNotNull(result.getUpdateTime());
    }

    @Test
    void disableAndDeleteCreateDeleteTasksForActiveVersion() {
        var disabled = document(9, 3, 2);
        when(documents.findIncludingDeletedForUpdate(9)).thenReturn(disabled);
        when(documents.update(any())).thenReturn(1);
        service.disable(9, 7);
        verify(outbox).insert(argThat(task -> "DELETE".equals(task.getOperation()) && task.getDocumentVersion() == 2));
    }

    @Test
    void rebuildCreatesOneRealCollectionTask() {
        assertEquals(1, service.rebuild(7));
        verify(outbox).insert(argThat(task -> "REBUILD".equals(task.getOperation())
                && task.getDocumentId() == 0 && task.getDocumentVersion() == 0
                && task.getCreateTime() != null && task.getUpdateTime() != null));
        verify(documents, never()).listRebuildableLimited(anyInt());
    }

    @Test
    void retriesOnlyCurrentFailedDeleteTaskForDisabledDocument() {
        var disabled = document(9, 3, 2);
        disabled.setStatus("DISABLED");
        when(documents.findIncludingDeleted(9)).thenReturn(disabled);
        var task = CustomerServiceKnowledgeEtlOutbox.builder().id(21L).documentId(9L)
                .documentVersion(2L).etlVersion(4L).operation("DELETE").status("FAILED").build();
        when(outbox.selectOneById(21L)).thenReturn(task);
        when(outbox.requeueFailedDelete(eq(21L), eq(9L), eq(2L), eq(4L), any())).thenReturn(1);

        service.retryFailedDelete(9L, 21L, 7L);

        verify(outbox).requeueFailedDelete(eq(21L), eq(9L), eq(2L), eq(4L), any());
    }

    @Test
    void rejectsRetryForStaleFailedDeleteTask() {
        var disabled = document(9, 3, 2);
        disabled.setStatus("DISABLED");
        when(documents.findIncludingDeleted(9)).thenReturn(disabled);
        var task = CustomerServiceKnowledgeEtlOutbox.builder().id(21L).documentId(9L)
                .documentVersion(2L).etlVersion(3L).operation("DELETE").status("FAILED").build();
        when(outbox.selectOneById(21L)).thenReturn(task);

        assertThrows(RuntimeException.class, () -> service.retryFailedDelete(9L, 21L, 7L));
        verify(outbox, never()).requeueFailedDelete(anyLong(), anyLong(), anyLong(), anyLong(), any());
    }

    @Test
    void replacementAndDetailRejectSoftDeletedDocument() {
        var deleted = document(9, 3, 2); deleted.setIsDelete(1);
        when(documents.findIncludingDeletedForUpdate(9)).thenReturn(deleted);
        when(documents.findIncludingDeleted(9)).thenReturn(deleted);
        assertThrows(RuntimeException.class, () -> service.upload(
                new MockMultipartFile("file", "guide.txt", "text/plain", "data".getBytes()), 9L, 7));
        assertThrows(RuntimeException.class, () -> service.detail(9));
        verify(oss).deleteKnowledgeObject(any());
        verify(documents, never()).update(any());
    }

    @Test
    void disabledReindexCannotImplicitlyReactivate() {
        var disabled = document(9, 3, 2); disabled.setStatus("DISABLED");
        when(documents.findIncludingDeletedForUpdate(9)).thenReturn(disabled);
        assertThrows(RuntimeException.class, () -> service.reindex(9, 7));
        verify(documents, never()).update(any());
        verifyNoInteractions(outbox);
    }

    @Test
    void enableStartsNewActivationButRetainsOldServingIdentity() {
        var disabled = document(9, 3, 2); disabled.setStatus("DISABLED");
        when(documents.findIncludingDeletedForUpdate(9)).thenReturn(disabled);
        var enabled = service.enable(9, 7);
        assertEquals(3L, enabled.getActivationRevision());
        assertEquals(5L, enabled.getEtlVersion());
        assertEquals(2L, enabled.getIndexedActivationRevision());
        assertEquals(3L, enabled.getIndexedEtlVersion());
        assertEquals(2L, enabled.getIndexedVersion());
    }

    @Test
    void disableKeepsActivationAsDeleteUpperBound() {
        var active = document(9, 3, 2); active.setStatus("ACTIVE");
        when(documents.findIncludingDeletedForUpdate(9)).thenReturn(active);
        var result = service.disable(9, 7);
        assertEquals(2L, result.getActivationRevision());
        assertEquals(5L, result.getEtlVersion());
    }

    @Test
    void replacingDisabledDocumentCreatesNewActivationAndRegistersNewContent() {
        var disabled = document(9, 3, 2); disabled.setStatus("DISABLED");
        when(documents.findIncludingDeletedForUpdate(9)).thenReturn(disabled);
        when(documents.update(any())).thenReturn(1);
        var result = service.upload(new MockMultipartFile("file", "guide.txt", "text/plain", "data".getBytes()), 9L, 7);
        assertEquals(3L, result.getActivationRevision());
        assertEquals(2L, result.getIndexedActivationRevision());
        verify(documents).insertContentRevision(argThat(d -> d.getDocumentVersion() == 4L && d.getObjectKey().endsWith(".txt")));
        verify(oss, never()).deleteKnowledgeObject("old-key");
    }

    @Test
    void registryFailureCompensatesNewObjectAndDoesNotEnqueue() {
        when(documents.insertContentRevision(any())).thenReturn(0);
        assertThrows(IllegalStateException.class, () -> service.upload(
                new MockMultipartFile("file", "guide.txt", "text/plain", "data".getBytes()), null, 7));
        verify(oss).deleteKnowledgeObject(any());
        verifyNoInteractions(outbox);
    }

    private static CustomerServiceKnowledgeDocument document(long id, long version, long indexed) {
        var document = new CustomerServiceKnowledgeDocument();
        document.setId(id);
        document.setDocumentVersion(version);
        document.setIndexedVersion(indexed);
        document.setEtlVersion(4L);
        document.setActivationRevision(2L);
        document.setIndexedEtlVersion(3L);
        document.setIndexedActivationRevision(2L);
        document.setChunkCount(2);
        document.setIsDelete(0);
        return document;
    }
}
