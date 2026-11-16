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

/**
 * 从 Spring outbox 领取知识库任务，校验租约后调用 Python 完成索引、删除或重建。
 */
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
    @Autowired
    private com.yupi.yuaicodemother.service.KnowledgePublicationService publicationService;
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
        if(publicationService!=null) publicationService.requireConfiguredMode();
        log.info("Knowledge task started: taskId={}, documentId={}, operation={}, retryCount={}",
                task.getId(), task.getDocumentId(), task.getOperation(), task.getRetryCount());
        String operationId = "task_" + task.getId() + "_" + UUID.randomUUID().toString().replace("-", "");
        KnowledgeMutationCoordinator.Lease lease = null;
        try {
            if(properties.isPublicationEnabled()) {
                try {
                    if(publicationService.findTaskFact(task.getId())!=null) return;
                } catch(RuntimeException unknown) { throw new TaskFinalizationException(unknown); }
            }
            PreparedOperation prepared = prepare(task);
            refreshClaim(task);
            String scope = "REBUILD".equals(task.getOperation())
                    ? "collection:" + properties.getCollectionAlias() : "document:" + task.getDocumentId();
            lease = coordinator.acquire(scope, operationId, task.getOperation(), owner);
            if(properties.isPublicationEnabled()) {
                executePublication(task,prepared,lease);
                return;
            }
            switch (task.getOperation()) {
                case "INDEX" -> executeIndex(task, (PreparedIndex) prepared, lease);
                case "DELETE" -> executeDelete(task, (PreparedDelete) prepared, lease);
                case "REBUILD" -> executeRebuild(task, (PreparedRebuild) prepared, lease);
                default -> throw new CustomerServiceAiClient.CallException("KNOWLEDGE_TASK_OPERATION_INVALID", false);
            }
            if (!"INDEX".equals(task.getOperation()))
                completeNonIndexTask(task);
            log.info("Knowledge task succeeded: taskId={}, documentId={}, operation={}",
                    task.getId(), task.getDocumentId(), task.getOperation());
        } catch (LostClaimException ignored) {
            log.info("Knowledge task claim lost: taskId={}", task.getId());
            // 准备或回执落账期间失去所有权，旧执行不得重写替代所有者的任务状态。
        } catch (StaleTaskException error) {
            log.info("Knowledge task skipped as stale: taskId={}", task.getId());
            finishSafely(task, "SKIPPED", "KNOWLEDGE_TASK_STALE");
        } catch (RebuildSnapshotChangedException error) {
            fail(task, "KNOWLEDGE_REBUILD_SNAPSHOT_CHANGED", true);
        } catch (TaskFinalizationException error) {
            logFinalizationFailure(task, "INDEX".equals(task.getOperation())
                    ? "KNOWLEDGE_INDEX_FINALIZATION_FAILED" : "KNOWLEDGE_TASK_FINALIZATION_FAILED", error.getCause());
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
                    // 租约有明确的过期时间；撤销失败不能中断 worker 主循环。
                }
            }
        }
    }

    private void executePublication(CustomerServiceKnowledgeEtlOutbox task, PreparedOperation prepared,
                                    KnowledgeMutationCoordinator.Lease lease) {
        if(publicationService==null) throw new IllegalStateException("KNOWLEDGE_PUBLICATION_NOT_READY");
        var context=publicationService.prepare(task.getId(),owner,lease);
        Object mutation;
        if(prepared instanceof PreparedIndex index) {
            var document=index.document();
            if(documentMapper.markIndexing(document.getId(),task.getDocumentVersion(),task.getEtlVersion())!=1)
                throw new StaleTaskException();
            mutation=new CustomerServiceAiClient.IndexRequest(String.valueOf(document.getId()),task.getDocumentVersion(),
                    document.getName(),document.getFileType(),index.signedUrl(),document.getContentHash(),
                    String.valueOf(task.getEtlVersion()),task.getEtlVersion(),document.getActivationRevision(),lease);
        } else if(prepared instanceof PreparedDelete delete) {
            mutation=new CustomerServiceAiClient.DeleteRequest(String.valueOf(task.getDocumentId()),task.getDocumentVersion(),
                    delete.snapshot().activationRevision(),lease);
        } else if(prepared instanceof PreparedRebuild rebuild) {
            List<RebuildDocumentSnapshot> current=rebuildSnapshot();
            if(!rebuild.documents().equals(current)) throw new RebuildSnapshotChangedException();
            List<CustomerServiceAiClient.RebuildDocument> documents=current.stream().map(document ->
                    new CustomerServiceAiClient.RebuildDocument(String.valueOf(document.documentId()),document.documentVersion(),
                            document.name(),document.fileType(),ossManager.generateKnowledgeDownloadUrl(document.objectKey()).toString(),
                            document.contentHash(),document.indexedEtlVersion(),document.activationRevision())).toList();
            mutation=new CustomerServiceAiClient.RebuildRequest(properties.getCollectionAlias(),documents,rebuild.etlVersion(),lease);
        } else throw new IllegalStateException("KNOWLEDGE_TASK_OPERATION_INVALID");
        var receipt=aiClient.preparePublication(new com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.CandidateRequest(context,mutation));
        try {
            publicationService.commit(task.getId(),owner,lease,context,receipt);
        } catch(IllegalStateException rejected) {
            // 明确的协议拒绝可重试；数据库确认未知绝不能被写成失败。
            if(rejected.getMessage()!=null && rejected.getMessage().startsWith("KNOWLEDGE_")) throw rejected;
            reconcileUnknownCommit(task,receipt,rejected);
        } catch(RuntimeException unknown) {
            reconcileUnknownCommit(task,receipt,unknown);
        }
    }

    private void reconcileUnknownCommit(CustomerServiceKnowledgeEtlOutbox task,
            com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.CandidateReceipt receipt,
            RuntimeException unknown) {
        try {
            var fact=publicationService.findTaskFact(task.getId());
            if(fact!=null && fact.receipt().equals(receipt)) return;
        } catch(RuntimeException unavailable) {
            // 事实查询也失败时继续保留未知状态，禁止把已提交发布误记为执行失败。
        }
        throw new TaskFinalizationException(unknown);
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
        if (document.getActivationRevision() == null || document.getActivationRevision() <= 0
                || task.getEtlVersion() == null || task.getEtlVersion() <= 0)
            throw new CustomerServiceAiClient.CallException("KNOWLEDGE_INDEX_REVISION_INVALID", false);
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

    private void completeNonIndexTask(CustomerServiceKnowledgeEtlOutbox task) {
        int changed;
        try {
            changed = outboxMapper.finish(task.getId(), owner, "SUCCEEDED", null);
        } catch (RuntimeException error) {
            // RPC 已成功，数据库写入或确认失败不能再当成执行失败写入重试状态。
            throw new TaskFinalizationException(error);
        }
        if (changed != 1) throw new LostClaimException();
    }

    private void executeIndex(CustomerServiceKnowledgeEtlOutbox task, PreparedIndex prepared,
                              KnowledgeMutationCoordinator.Lease lease) {
        CustomerServiceKnowledgeDocument document = prepared.document();
        if (documentMapper.markIndexing(document.getId(), task.getDocumentVersion(), task.getEtlVersion()) != 1)
            throw new StaleTaskException();
        CustomerServiceAiClient.Result result = aiClient.index(new CustomerServiceAiClient.IndexRequest(
                String.valueOf(document.getId()), task.getDocumentVersion(), document.getName(), document.getFileType(),
                prepared.signedUrl(), document.getContentHash(), String.valueOf(task.getEtlVersion()),
                task.getEtlVersion(), document.getActivationRevision(), lease));
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
                String.valueOf(task.getDocumentId()), task.getDocumentVersion(), current.activationRevision(), lease));
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
                        document.contentHash(), document.indexedEtlVersion(), document.activationRevision())).toList();
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
                document.getEtlVersion(), document.getStatus(), document.getIsDelete(), targetVersion, document.getActivationRevision());
    }

    private List<RebuildDocumentSnapshot> rebuildSnapshot() {
        int maximumDocuments = properties.getRebuildMaxDocuments();
        int queryLimit = Math.addExact(maximumDocuments, 1);
        List<CustomerServiceKnowledgeDocument> documents = documentMapper.listRebuildableLimited(queryLimit);
        if (documents.size() > maximumDocuments)
            throw new CustomerServiceAiClient.CallException("KNOWLEDGE_REBUILD_TOO_MANY_DOCUMENTS", false);
        for (CustomerServiceKnowledgeDocument document : documents) {
            if (document.getObjectKey() == null || document.getName() == null || document.getFileType() == null
                    || document.getContentHash() == null || document.getIndexedEtlVersion() == null
                    || document.getIndexedEtlVersion() <= 0 || document.getIndexedActivationRevision() == null
                    || document.getIndexedActivationRevision() <= 0)
                throw new CustomerServiceAiClient.CallException("KNOWLEDGE_REBUILD_CONTENT_REVISION_MISSING", false);
        }
        return documents.stream().map(document ->
                new RebuildDocumentSnapshot(document.getId(), document.getIndexedVersion(), document.getEtlVersion(),
                        document.getIndexedVersion(), document.getName(), document.getFileType(), document.getObjectKey(),
                        document.getContentHash(), document.getStatus(), document.getIsDelete(),
                        document.getIndexedEtlVersion(), document.getIndexedActivationRevision())).toList();
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
        log.warn("Knowledge worker finalization outcome unknown; reconcile persisted state before retry: taskId={}, operation={}, errorCode={}, persistenceError={}",
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
            // 后续仍可能重新认领过期任务，此处不能终止轮询循环。
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
                                  String status, Integer isDelete, Long targetVersion, Long activationRevision) { }
    private record RebuildDocumentSnapshot(Long documentId, Long documentVersion, Long etlVersion,
                                           Long indexedVersion, String name, String fileType, String objectKey,
                                           String contentHash, String status, Integer isDelete, Long indexedEtlVersion,
                                           Long activationRevision) { }
}
