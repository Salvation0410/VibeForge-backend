package com.yupi.yuaicodemother.worker;

import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeEtlOutboxMapper;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeEtlOutbox;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.UUID;

@Component
@ConditionalOnProperty(prefix = "ai.customer-service", name = "enabled", havingValue = "true")
public class CustomerServiceKnowledgeEtlWorker {
    private final CustomerServiceKnowledgeDocumentMapper documentMapper;
    private final CustomerServiceKnowledgeEtlOutboxMapper outboxMapper;
    private final OssManager ossManager;
    private final CustomerServiceAiClient aiClient;
    private final KnowledgeMutationCoordinator coordinator;
    private final CustomerServiceProperties properties;
    private final Clock clock;
    private final String owner = UUID.randomUUID().toString();

    public CustomerServiceKnowledgeEtlWorker(CustomerServiceKnowledgeDocumentMapper documentMapper,
                                             CustomerServiceKnowledgeEtlOutboxMapper outboxMapper,
                                             OssManager ossManager, CustomerServiceAiClient aiClient,
                                             KnowledgeMutationCoordinator coordinator,
                                             CustomerServiceProperties properties) {
        this(documentMapper, outboxMapper, ossManager, aiClient, coordinator, properties, Clock.systemUTC());
    }

    public CustomerServiceKnowledgeEtlWorker(CustomerServiceKnowledgeDocumentMapper documentMapper,
                                      CustomerServiceKnowledgeEtlOutboxMapper outboxMapper,
                                      OssManager ossManager, CustomerServiceAiClient aiClient,
                                      KnowledgeMutationCoordinator coordinator,
                                      CustomerServiceProperties properties, Clock clock) {
        this.documentMapper = documentMapper;
        this.outboxMapper = outboxMapper;
        this.ossManager = ossManager;
        this.aiClient = aiClient;
        this.coordinator = coordinator;
        this.properties = properties;
        this.clock = clock;
    }

    @Scheduled(fixedDelayString = "${ai.customer-service.poll-interval-millis:5000}")
    public void poll() {
        LocalDateTime now = now();
        for (CustomerServiceKnowledgeEtlOutbox task : outboxMapper.findClaimCandidates(now, properties.getBatchSize())) {
            LocalDateTime deadline = now.plusSeconds(Math.max(1, properties.getClaimTimeoutSeconds()));
            if (outboxMapper.claim(task.getId(), owner, now, deadline) == 1) execute(task);
        }
    }

    public void execute(CustomerServiceKnowledgeEtlOutbox task) {
        String operationId = "task_" + task.getId() + "_" + UUID.randomUUID().toString().replace("-", "");
        KnowledgeMutationCoordinator.Lease lease = null;
        try {
            PreparedRebuild preparedRebuild = "REBUILD".equals(task.getOperation()) ? prepareRebuild(task) : null;
            String scope = "REBUILD".equals(task.getOperation())
                    ? "collection:" + properties.getCollectionAlias() : "document:" + task.getDocumentId();
            lease = coordinator.acquire(scope, operationId, task.getOperation(), owner);
            switch (task.getOperation()) {
                case "INDEX" -> executeIndex(task, lease);
                case "DELETE" -> executeDelete(task, lease);
                case "REBUILD" -> executeRebuild(preparedRebuild, lease);
                default -> throw new CustomerServiceAiClient.CallException("KNOWLEDGE_TASK_OPERATION_INVALID", false);
            }
            outboxMapper.finish(task.getId(), owner, "SUCCEEDED", null);
        } catch (StaleTaskException error) {
            outboxMapper.finish(task.getId(), owner, "SKIPPED", "KNOWLEDGE_TASK_STALE");
        } catch (CustomerServiceAiClient.CallException error) {
            fail(task, error.code(), error.transientFailure());
        } catch (IllegalStateException error) {
            fail(task, stableCoordinatorCode(error), true);
        } catch (RuntimeException error) {
            fail(task, "KNOWLEDGE_WORKER_FAILED", true);
        } finally {
            if (lease != null) coordinator.revoke(lease);
        }
    }

    private void executeIndex(CustomerServiceKnowledgeEtlOutbox task, KnowledgeMutationCoordinator.Lease lease) {
        CustomerServiceKnowledgeDocument document = documentMapper.findIncludingDeleted(task.getDocumentId());
        if (document == null || !task.getDocumentVersion().equals(document.getDocumentVersion())
                || !task.getEtlVersion().equals(document.getEtlVersion()) || Integer.valueOf(1).equals(document.getIsDelete())) {
            throw new StaleTaskException();
        }
        if (documentMapper.markIndexing(document.getId(), task.getDocumentVersion(), task.getEtlVersion()) != 1)
            throw new StaleTaskException();
        String signedUrl = ossManager.generateKnowledgeDownloadUrl(document.getObjectKey()).toString();
        CustomerServiceAiClient.Result result = aiClient.index(new CustomerServiceAiClient.IndexRequest(
                String.valueOf(document.getId()), task.getDocumentVersion(), document.getName(), document.getFileType(),
                signedUrl, document.getContentHash(), String.valueOf(task.getEtlVersion()), lease));
        if (documentMapper.completeIndex(document.getId(), task.getDocumentVersion(), task.getEtlVersion(), result.chunkCount()) != 1)
            throw new StaleTaskException();
    }

    private void executeDelete(CustomerServiceKnowledgeEtlOutbox task, KnowledgeMutationCoordinator.Lease lease) {
        CustomerServiceKnowledgeDocument document = documentMapper.findIncludingDeleted(task.getDocumentId());
        if (document == null || !task.getEtlVersion().equals(document.getEtlVersion())) throw new StaleTaskException();
        aiClient.delete(new CustomerServiceAiClient.DeleteRequest(
                String.valueOf(task.getDocumentId()), task.getDocumentVersion(), lease));
    }

    private PreparedRebuild prepareRebuild(CustomerServiceKnowledgeEtlOutbox task) {
        List<CustomerServiceAiClient.RebuildDocument> documents = documentMapper.listAllActive().stream().map(document ->
                new CustomerServiceAiClient.RebuildDocument(String.valueOf(document.getId()), document.getDocumentVersion(),
                        document.getName(), document.getFileType(),
                        ossManager.generateKnowledgeDownloadUrl(document.getObjectKey()).toString(), document.getContentHash())).toList();
        return new PreparedRebuild(documents, String.valueOf(task.getEtlVersion()));
    }

    private void executeRebuild(PreparedRebuild prepared, KnowledgeMutationCoordinator.Lease lease) {
        aiClient.rebuild(new CustomerServiceAiClient.RebuildRequest(properties.getCollectionAlias(),
                prepared.documents(), prepared.etlVersion(), lease));
    }

    private void fail(CustomerServiceKnowledgeEtlOutbox task, String code, boolean transientFailure) {
        int attempts = (task.getRetryCount() == null ? 0 : task.getRetryCount()) + 1;
        boolean retry = transientFailure && attempts < Math.max(1, properties.getRetryMax());
        long delay = Math.multiplyExact(Math.max(1, properties.getRetryBaseDelaySeconds()), 1L << Math.min(20, attempts - 1));
        outboxMapper.retry(task.getId(), owner, retry ? "PENDING" : "FAILED", attempts,
                now().plusSeconds(retry ? delay : 0), code);
        if (!retry && "INDEX".equals(task.getOperation()))
            documentMapper.failIndex(task.getDocumentId(), task.getDocumentVersion(), task.getEtlVersion(), code);
    }

    private LocalDateTime now() { return LocalDateTime.ofInstant(clock.instant(), ZoneOffset.UTC); }

    private static String stableCoordinatorCode(IllegalStateException error) {
        String message = error.getMessage();
        return message != null && message.matches("KNOWLEDGE_[A-Z0-9_]+") ? message : "KNOWLEDGE_COORDINATOR_UNAVAILABLE";
    }

    private static final class StaleTaskException extends RuntimeException { }
    private record PreparedRebuild(List<CustomerServiceAiClient.RebuildDocument> documents, String etlVersion) { }
}
