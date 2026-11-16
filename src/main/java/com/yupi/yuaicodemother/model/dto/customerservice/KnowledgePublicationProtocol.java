package com.yupi.yuaicodemother.model.dto.customerservice;

import com.fasterxml.jackson.databind.annotation.JsonSerialize;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator.LeaseLongSerializer;
import java.util.List;

/** 内部发布协议独立于前端 ID 字符串配置，所有修订与 epoch 必须是 JSON 数字。 */
public final class KnowledgePublicationProtocol {
    private KnowledgePublicationProtocol() { }
    public record DocumentIdentity(String documentId,
            @JsonSerialize(using=LeaseLongSerializer.class) long documentVersion,
            @JsonSerialize(using=LeaseLongSerializer.class) long indexRevision,
            @JsonSerialize(using=LeaseLongSerializer.class) long activationRevision,
            String sha256, String fileName, String fileType) { }
    public record ReceiptDocument(String documentId,
            @JsonSerialize(using=LeaseLongSerializer.class) long documentVersion,
            @JsonSerialize(using=LeaseLongSerializer.class) long indexRevision,
            @JsonSerialize(using=LeaseLongSerializer.class) long activationRevision,
            String sha256, String fileName, String fileType, int chunkCount) {
        public DocumentIdentity identity() { return new DocumentIdentity(documentId, documentVersion,
                indexRevision, activationRevision, sha256, fileName, fileType); }
    }
    public record PublicationContext(String publicationId,
            @JsonSerialize(using=LeaseLongSerializer.class) long baseEpoch,
            String sourceCollection, List<DocumentIdentity> documents) { }
    public record CandidateRequest(PublicationContext context, Object mutation) { }
    public record CandidateReceipt(String publicationId,
            @JsonSerialize(using=LeaseLongSerializer.class) long baseEpoch,
            String operationId, @JsonSerialize(using=LeaseLongSerializer.class) long fence,
            String candidateCollection, String snapshotHash, String embeddingModelVersion,
            int embeddingDimension, List<ReceiptDocument> documents) { }
    public record ReadSnapshot(boolean enabled, @JsonSerialize(using=LeaseLongSerializer.class) long epoch,
            String publicationId, String collectionName, String snapshotHash, String embeddingModelVersion,
            int embeddingDimension, List<DocumentIdentity> documents) { }
    public record ReconcileAliasRequest(String collectionAlias, String collectionName, String publicationId,
            @JsonSerialize(using=LeaseLongSerializer.class) long epoch) { }
    public record PublicationFact(String publicationId,
            @JsonSerialize(using=LeaseLongSerializer.class) long epoch, CandidateReceipt receipt) { }
}
