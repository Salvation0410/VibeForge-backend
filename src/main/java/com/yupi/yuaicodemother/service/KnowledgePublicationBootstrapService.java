package com.yupi.yuaicodemother.service;

import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.*;
import org.springframework.stereotype.Service;
import java.util.UUID;

/** 维护入口只生产复用原向量的 REBUILD 候选，网络调用不得占用发布行锁。 */
@Service
public class KnowledgePublicationBootstrapService {
    private final KnowledgePublicationService publications;
    private final KnowledgeMutationCoordinator coordinator;
    private final CustomerServiceAiClient client;
    private final CustomerServiceProperties properties;
    private final OssManager oss;
    public KnowledgePublicationBootstrapService(KnowledgePublicationService publications,KnowledgeMutationCoordinator coordinator,
            CustomerServiceAiClient client,CustomerServiceProperties properties,OssManager oss) {
        this.publications=publications; this.coordinator=coordinator; this.client=client; this.properties=properties; this.oss=oss;
    }
    public PublicationFact bootstrap(String source) {
        if(!properties.isPublicationEnabled() || !properties.isPublicationBootstrapAllowed())
            throw new IllegalStateException("KNOWLEDGE_PUBLICATION_BOOTSTRAP_FORBIDDEN");
        String id="bootstrap_"+UUID.randomUUID().toString().replace("-", "");
        var lease=coordinator.acquire("collection:"+properties.getCollectionAlias(),id,"REBUILD",id);
        try {
            PublicationContext context=publications.prepareBootstrap(id,source,lease);
            var registry=publications.servingRegistry();
            var documents=registry.stream().map(d -> new CustomerServiceAiClient.RebuildDocument(String.valueOf(d.getId()),
                    d.getIndexedVersion(),d.getName(),d.getFileType().toUpperCase(java.util.Locale.ROOT),
                    oss.generateKnowledgeDownloadUrl(d.getObjectKey()).toString(),d.getContentHash(),
                    d.getIndexedEtlVersion(),d.getIndexedActivationRevision())).toList();
            var request=new CandidateRequest(context,new CustomerServiceAiClient.RebuildRequest(
                    properties.getCollectionAlias(),documents,id,lease));
            CandidateReceipt receipt=client.preparePublication(request);
            return publications.commitBootstrap(context,receipt,lease);
        } finally {
            try { coordinator.revoke(lease); } catch(RuntimeException ignored) {
                // 首次 publication 事实一经提交不可因租约清理失败撤销；租约本身有明确截止时间。
            }
        }
    }
}
