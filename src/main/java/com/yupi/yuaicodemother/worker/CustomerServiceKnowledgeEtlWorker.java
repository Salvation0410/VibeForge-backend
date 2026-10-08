package com.yupi.yuaicodemother.worker;

import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeEtlOutboxMapper;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeEtlOutbox;
import com.yupi.yuaicodemother.service.CustomerServiceKnowledgeTaskFinalizer;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Clock;
import java.time.DateTimeException;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Objects;
import java.util.UUID;

@Component
@ConditionalOnProperty(prefix = "ai.customer-service", name = "enabled", havingValue = "true")
public class CustomerServiceKnowledgeEtlWorker {
    private static final Logger log = LoggerFactory.getLogger(CustomerServiceKnowledgeEtlWorker.class);
    private static final long MAX_RETRY_DELAY_SECONDS = 3600;
    private static final LocalDateTime MAX_DATABASE_TIME = LocalDateTime.of(9999, 12, 31, 23, 59, 59);
    private final CustomerServiceKnowledgeDocumentMapper documentMapper;
    private final CustomerServiceKnowledgeEtlOutboxMapper outboxMapper;
    private final OssManager ossManager;
    private final CustomerServiceAiClient aiClient;
    private final KnowledgeMutationCoordinator coordinator;
    private final CustomerServiceKnowledgeTaskFinalizer taskFinalizer;
    private final CustomerServiceProperties properties;
    private final Clock clock;
    private final String owner = UUID.randomUUID().toString();

    @Autowired
    public CustomerServiceKnowledgeEtlWorker(CustomerServiceKnowledgeDocumentMapper documentMapper,
                                             CustomerServiceKnowledgeEtlOutboxMapper outboxMapper,
                                             OssManager ossManager, CustomerServiceAiClient aiClient,
                                             KnowledgeMutationCoordinator coordinator,
                                             CustomerServiceKnowledgeTaskFinalizer taskFinalizer,
                                             CustomerServiceProperties properties) {
        this(documentMapper, outboxMapper, ossManager, aiClient, coordinator, taskFinalizer,
                properties, Clock.systemUTC());
    }

    public CustomerServiceKnowledgeEtlWorker(CustomerServiceKnowledgeDocumentMapper documentMapper,
                                      CustomerServiceKnowledgeEtlOutboxMapper outboxMapper,
                                      OssManager ossManager, CustomerServiceAiClient aiClient,
                                      KnowledgeMutationCoordinator coordinator,
                                      CustomerServiceKnowledgeTaskFinalizer taskFinalizer,
                                      CustomerServiceProperties properties, Clock clock) {
        this.documentMapper = documentMapper;
        this.outboxMapper = outboxMapper;
        this.ossManager = ossManager;
        this.aiClient = aiClient;
        this.coordinator = coordinator;
        this.taskFinalizer = taskFinalizer;
        this.properties = properties;
        this.clock = clock;
    }

    @PostConstruct
    public void logStartup() {
        log.info("Knowledge worker enabled: pollIntervalMillis={}, batchSize={}, schedulingTimezone=UTC",
                properties.getPollIntervalMillis(), properties.getBatchSize());
    }

    @Scheduled(fixedDelayString = "${ai.customer-service.poll-interval-millis:5000}")
    public void poll() {
        LocalDateTime scanNow = now();
        for (CustomerServiceKnowledgeEtlOutbox task : outboxMapper.findClaimCandidates(scanNow, properties.getBatchSize())) {
            LocalDateTime claimNow = now();
            LocalDateTime deadline = claimNow.plusSeconds(Math.max(1, properties.getClaimTimeoutSeconds()));
            if (outboxMapper.claim(task.getId(), owner, claimNow, deadline) == 1) execute(task);
        }
    }

    public void execute(CustomerServiceKnowledgeEtlOutbox task) {
        log.info("Knowledge task started: taskId={}, documentId={}, operation={}, retryCount={}",
                task.getId(), task.getDocumentId(), task.getOperation(), task.getRetryCount());
        String operationId = "task_" + task.getId() + "_" + UUID.randomUUID().toString().replace("-", "");
        KnowledgeMutationCoordinator.Lease lease = null;
        try {
            PreparedOperation prepared = prepare(task);
            refreshClaim(task);
            String scope = "REBUILD".equals(task.getOperation())
                    ? "collection:" + properties.getCollectionAlias() : "document:" + task.getDocumentId();
            lease = coordinator.acquire(scope, operationId, task.getOperation(), owner);
            switch (task.getOperation()) {
                case "INDEX" -> executeIndex(task, (PreparedIndex) prepared, lease);
                case "DELETE" -> executeDelete(task, (PreparedDelete) prepared, lease);
                case "REBUILD" -> executeRebuild(task, (PreparedRebuild) prepared, lease);
                default -> throw new CustomerServiceAiClient.CallException("KNOWLEDGE_TASK_OPERATION_INVALID", false);
            }
            if (!"INDEX".equals(task.getOperation()))
                outboxMapper.finish(task.getId(), owner, "SUCCEEDED", null);
            log.info("Knowledge task succeeded: taskId={}, documentId={}, operation={}",
                    task.getId(), task.getDocumentId(), task.getOperation());
        } catch (LostClaimException ignored) {
            log.info("Knowledge task claim lost: taskId={}", task.getId());
            // Another worker reclaimed the task while this worker was preparing the request.
        } catch (StaleTaskException error) {
            log.info("Knowledge task skipped as stale: taskId={}", task.getId());
            finishSafely(task, "SKIPPED", "KNOWLEDGE_TASK_STALE");
        } catch (RebuildSnapshotChangedException error) {
            fail(task, "KNOWLEDGE_REBUILD_SNAPSHOT_CHANGED", true);
        } catch (TaskFinalizationException error) {
            logFinalizationFailure(task, "KNOWLEDGE_INDEX_FINALIZATION_FAILED", error.getCause());
        } catch (CustomerServiceAiClient.CallException error) {
            fail(task, error.code(), error.transientFailure());
        } catch (IllegalStateException error) {
            fail(task, stableCoordinatorCode(error), true);
        } catch (RuntimeException error) {
            log.error("Knowledge task unexpected failure: taskId={}, documentId={}, errorType={}",
                    task.getId(), task.getDocumentId(), error.getClass().getSimpleName());
            fail(task, "KNOWLEDGE_WORKER_FAILED", true);
        } finally {
            if (lease != null) {
                try {
                    coordinator.revoke(lease);
                } catch (RuntimeException ignored) {
                    // The lease has a bounded expiry; revoke failure must not stop the worker loop.
                }
            }
        }
    }

    private PreparedOperation prepare(CustomerServiceKnowledgeEtlOutbox task) {
        return switch (task.getOperation()) {
            case "INDEX" -> prepareIndex(task);
            case "DELETE" -> prepareDelete(task);
            case "REBUILD" -> prepareRebuild(task);
            default -> throw new CustomerServiceAiClient.CallException("KNOWLEDGE_TASK_OPERATION_INVALID", false);
        };
    }

    private PreparedIndex prepareIndex(CustomerServiceKnowledgeEtlOutbox task) {
        CustomerServiceKnowledgeDocument document = documentMapper.findIncludingDeleted(task.getDocumentId());
        if (document == null || !task.getDocumentVersion().equals(document.getDocumentVersion())
                || !task.getEtlVersion().equals(document.getEtlVersion()) || Integer.valueOf(1).equals(document.getIsDelete())) {
            throw new StaleTaskException();
        }
        String signedUrl = ossManager.generateKnowledgeDownloadUrl(document.getObjectKey()).toString();
        return new PreparedIndex(document, signedUrl);
    }

    private PreparedDelete prepareDelete(CustomerServiceKnowledgeEtlOutbox task) {
        return new PreparedDelete(deleteSnapshot(task,
                documentMapper.findIncludingDeleted(task.getDocumentId())));
    }

    private void refreshClaim(CustomerServiceKnowledgeEtlOutbox task) {
        LocalDateTime deadline = now().plusSeconds(Math.max(1, properties.getClaimTimeoutSeconds()));
        if (outboxMapper.refreshClaim(task.getId(), owner, deadline) != 1) throw new LostClaimException();
    }

    private void executeIndex(CustomerServiceKnowledgeEtlOutbox task, PreparedIndex prepared,
                              KnowledgeMutationCoordinator.Lease lease) {
        CustomerServiceKnowledgeDocument document = prepared.document();
        if (documentMapper.markIndexing(document.getId(), task.getDocumentVersion(), task.getEtlVersion()) != 1)
            throw new StaleTaskException();
        CustomerServiceAiClient.Result result = aiClient.index(new CustomerServiceAiClient.IndexRequest(
                String.valueOf(document.getId()), task.getDocumentVersion(), document.getName(), document.getFileType(),
                prepared.signedUrl(), document.getContentHash(), String.valueOf(task.getEtlVersion()), lease));
        try {
            taskFinalizer.completeIndex(task.getId(), owner, document.getId(), task.getDocumentVersion(),
                    task.getEtlVersion(), result.chunkCount());
        } catch (RuntimeException error) {
            throw new TaskFinalizationException(error);
        }
    }

    private void executeDelete(CustomerServiceKnowledgeEtlOutbox task, PreparedDelete prepared,
                               KnowledgeMutationCoordinator.Lease lease) {
        DeleteSnapshot current = deleteSnapshot(task,
                documentMapper.findIncludingDeleted(task.getDocumentId()));
        if (!prepared.snapshot().equals(current)) throw new StaleTaskException();
        aiClient.delete(new CustomerServiceAiClient.DeleteRequest(
                String.valueOf(task.getDocumentId()), task.getDocumentVersion(), lease));
    }

    private PreparedRebuild prepareRebuild(CustomerServiceKnowledgeEtlOutbox task) {
        return new PreparedRebuild(rebuildSnapshot(), String.valueOf(task.getEtlVersion()));
    }

    private void executeRebuild(CustomerServiceKnowledgeEtlOutbox task, PreparedRebuild prepared,
                                KnowledgeMutationCoordinator.Lease lease) {
        List<RebuildDocumentSnapshot> current = rebuildSnapshot();
        if (!prepared.documents().equals(current)) throw new RebuildSnapshotChangedException();
        List<CustomerServiceAiClient.RebuildDocument> documents = current.stream().map(document ->
                new CustomerServiceAiClient.RebuildDocument(String.valueOf(document.documentId()),
                        document.documentVersion(), document.name(), document.fileType(),
                        ossManager.generateKnowledgeDownloadUrl(document.objectKey()).toString(),
                        document.contentHash())).toList();
        refreshClaim(task);
        long requiredLeaseExpiry = clock.instant()
                .plusSeconds(Math.max(1, properties.getTimeoutSeconds())).getEpochSecond();
        if (lease.expiresAt() <= requiredLeaseExpiry)
            throw new CustomerServiceAiClient.CallException("KNOWLEDGE_MUTATION_LEASE_WINDOW_EXPIRED", true);
        aiClient.rebuild(new CustomerServiceAiClient.RebuildRequest(properties.getCollectionAlias(),
                documents, prepared.etlVersion(), lease));
    }

    private DeleteSnapshot deleteSnapshot(CustomerServiceKnowledgeEtlOutbox task,
                                          CustomerServiceKnowledgeDocument document) {
        if (document == null || !Objects.equals(task.getDocumentId(), document.getId())
                || !Objects.equals(task.getEtlVersion(), document.getEtlVersion())) {
            throw new StaleTaskException();
        }
        boolean disabled = "DISABLED".equals(document.getStatus()) && Integer.valueOf(0).equals(document.getIsDelete());
        boolean deleting = "DELETING".equals(document.getStatus()) && Integer.valueOf(1).equals(document.getIsDelete());
        Long targetVersion = document.getIndexedVersion() != null && document.getIndexedVersion() > 0
                ? document.getIndexedVersion() : document.getDocumentVersion();
        if ((!disabled && !deleting) || !Objects.equals(task.getDocumentVersion(), targetVersion))
            throw new StaleTaskException();
        return new DeleteSnapshot(document.getId(), document.getDocumentVersion(), document.getIndexedVersion(),
                document.getEtlVersion(), document.getStatus(), document.getIsDelete(), targetVersion);
    }

    private List<RebuildDocumentSnapshot> rebuildSnapshot() {
        int maximumDocuments = properties.getRebuildMaxDocuments();
        int queryLimit = Math.addExact(maximumDocuments, 1);
        List<CustomerServiceKnowledgeDocument> documents = documentMapper.listRebuildableLimited(queryLimit);
        if (documents.size() > maximumDocuments)
            throw new CustomerServiceAiClient.CallException("KNOWLEDGE_REBUILD_TOO_MANY_DOCUMENTS", false);
        return documents.stream().map(document ->
                new RebuildDocumentSnapshot(document.getId(), document.getDocumentVersion(), document.getEtlVersion(),
                        document.getIndexedVersion(), document.getName(), document.getFileType(), document.getObjectKey(),
                        document.getContentHash(), document.getStatus(), document.getIsDelete())).toList();
    }

    private void fail(CustomerServiceKnowledgeEtlOutbox task, String code, boolean transientFailure) {
        try {
            int previousAttempts = task.getRetryCount() == null ? 0 : task.getRetryCount();
            int attempts = previousAttempts == Integer.MAX_VALUE ? Integer.MAX_VALUE : previousAttempts + 1;
            boolean retry = transientFailure && attempts < Math.max(1, properties.getRetryMax());
            LocalDateTime retryAt = computeRetryAt(attempts, retry);
            log.warn("Knowledge task failed: taskId={}, documentId={}, errorCode={}, attempt={}, willRetry={}, nextRetryTimeUtc={}",
                    task.getId(), task.getDocumentId(), code, attempts, retry, retryAt);
            if (!retry && "INDEX".equals(task.getOperation())) {
                taskFinalizer.failIndex(task.getId(), owner, task.getDocumentId(), task.getDocumentVersion(),
                        task.getEtlVersion(), attempts, retryAt, code);
            } else {
                outboxMapper.retry(task.getId(), owner, retry ? "PENDING" : "FAILED", attempts, retryAt, code);
            }
        } catch (RuntimeException persistenceError) {
            logFinalizationFailure(task, code, persistenceError);
        }
    }

    private void logFinalizationFailure(CustomerServiceKnowledgeEtlOutbox task, String code,
                                        Throwable persistenceError) {
        log.warn("Knowledge worker failure state was not persisted; task remains reclaimable: taskId={}, operation={}, errorCode={}, persistenceError={}",
                task.getId(), task.getOperation(), code, persistenceError.getClass().getSimpleName());
    }

    private LocalDateTime computeRetryAt(int attempts, boolean retry) {
        LocalDateTime current = now();
        if (!retry) return current;
        long multiplier = 1L << Math.min(62, Math.max(0, attempts - 1));
        long delay;
        try {
            delay = Math.multiplyExact(Math.max(1, properties.getRetryBaseDelaySeconds()), multiplier);
        } catch (ArithmeticException error) {
            delay = MAX_RETRY_DELAY_SECONDS;
        }
        delay = Math.min(delay, MAX_RETRY_DELAY_SECONDS);
        try {
            LocalDateTime retryAt = current.plusSeconds(delay);
            return retryAt.isAfter(MAX_DATABASE_TIME) ? MAX_DATABASE_TIME : retryAt;
        } catch (DateTimeException error) {
            return MAX_DATABASE_TIME;
        }
    }

    private void finishSafely(CustomerServiceKnowledgeEtlOutbox task, String status, String code) {
        try {
            outboxMapper.finish(task.getId(), owner, status, code);
        } catch (RuntimeException ignored) {
            // A later expired-claim reclaim remains possible; never terminate the poll loop here.
        }
    }

    private LocalDateTime now() { return LocalDateTime.ofInstant(clock.instant(), ZoneOffset.UTC); }

    private static String stableCoordinatorCode(IllegalStateException error) {
        String message = error.getMessage();
        return message != null && message.matches("KNOWLEDGE_[A-Z0-9_]+") ? message : "KNOWLEDGE_COORDINATOR_UNAVAILABLE";
    }

    private static final class StaleTaskException extends RuntimeException { }
    private static final class RebuildSnapshotChangedException extends RuntimeException { }
    private static final class LostClaimException extends RuntimeException { }
    private static final class TaskFinalizationException extends RuntimeException {
        private TaskFinalizationException(RuntimeException cause) { super(cause); }
    }
    private interface PreparedOperation { }
    private record PreparedIndex(CustomerServiceKnowledgeDocument document, String signedUrl) implements PreparedOperation { }
    private record PreparedDelete(DeleteSnapshot snapshot) implements PreparedOperation { }
    private record PreparedRebuild(List<RebuildDocumentSnapshot> documents,
                                   String etlVersion) implements PreparedOperation { }
    private record DeleteSnapshot(Long documentId, Long documentVersion, Long indexedVersion, Long etlVersion,
                                  String status, Integer isDelete, Long targetVersion) { }
    private record RebuildDocumentSnapshot(Long documentId, Long documentVersion, Long etlVersion,
                                           Long indexedVersion, String name, String fileType, String objectKey,
                                           String contentHash, String status, Integer isDelete) { }
}
