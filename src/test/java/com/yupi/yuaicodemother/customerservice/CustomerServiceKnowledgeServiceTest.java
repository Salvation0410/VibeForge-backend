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
        when(outbox.insert(any())).thenReturn(1);
        service = new CustomerServiceKnowledgeServiceImpl(documents, outbox, oss);
    }

    @Test
    void uploadCreatesDocumentAndOutbox() {
        var result = service.upload(new MockMultipartFile("file", "guide.txt", "text/plain", "data".getBytes()), null, 7);
        assertEquals(11, result.getId());
        assertEquals(1, result.getDocumentVersion());
        verify(documents).insert(any());
        verify(outbox).insert(argThat(task -> "INDEX".equals(task.getOperation()) && task.getDocumentVersion() == 1));
        verify(oss, never()).deleteKnowledgeObject(any());
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
                && task.getDocumentId() == 0 && task.getDocumentVersion() == 0));
        verify(documents, never()).listAllActive();
    }

    private static CustomerServiceKnowledgeDocument document(long id, long version, long indexed) {
        var document = new CustomerServiceKnowledgeDocument();
        document.setId(id);
        document.setDocumentVersion(version);
        document.setIndexedVersion(indexed);
        document.setEtlVersion(4L);
        document.setChunkCount(2);
        document.setIsDelete(0);
        return document;
    }
}
