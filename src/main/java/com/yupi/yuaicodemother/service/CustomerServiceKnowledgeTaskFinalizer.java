package com.yupi.yuaicodemother.service;

import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeEtlOutboxMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;

@Service
public class CustomerServiceKnowledgeTaskFinalizer {
    private static final String STALE_CODE = "KNOWLEDGE_TASK_STALE";
    private final CustomerServiceKnowledgeDocumentMapper documentMapper;
    private final CustomerServiceKnowledgeEtlOutboxMapper outboxMapper;

    public CustomerServiceKnowledgeTaskFinalizer(CustomerServiceKnowledgeDocumentMapper documentMapper,
                                                  CustomerServiceKnowledgeEtlOutboxMapper outboxMapper) {
        this.documentMapper = documentMapper;
        this.outboxMapper = outboxMapper;
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void completeIndex(long taskId, String owner, long documentId, long documentVersion,
                              long etlVersion, int chunkCount) {
        int updated = documentMapper.completeIndex(documentId, documentVersion, etlVersion, chunkCount);
        if (updated == 0) {
            requireUpdated(outboxMapper.finish(taskId, owner, "SKIPPED", STALE_CODE));
            return;
        }
        requireExactlyOne(updated);
        requireUpdated(outboxMapper.finish(taskId, owner, "SUCCEEDED", null));
    }

    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void failIndex(long taskId, String owner, long documentId, long documentVersion,
                          long etlVersion, int retryCount, LocalDateTime failedAt, String code) {
        int updated = documentMapper.failIndex(documentId, documentVersion, etlVersion, code);
        if (updated == 0) {
            requireUpdated(outboxMapper.finish(taskId, owner, "SKIPPED", STALE_CODE));
            return;
        }
        requireExactlyOne(updated);
        requireUpdated(outboxMapper.retry(taskId, owner, "FAILED", retryCount, failedAt, code));
    }

    private static void requireUpdated(int updated) {
        if (updated != 1) throw new IllegalStateException("KNOWLEDGE_TASK_FINALIZATION_CONFLICT");
    }

    private static void requireExactlyOne(int updated) {
        if (updated != 1) throw new IllegalStateException("KNOWLEDGE_DOCUMENT_FINALIZATION_CONFLICT");
    }
}
