package com.yupi.yuaicodemother.worker;

import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.ReconcileAliasRequest;
import com.yupi.yuaicodemother.service.KnowledgePublicationService;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

/** 周期核对当前 head，即使事件已 ACK 也修复迟到的旧镜像写入。 */
@Component
@ConditionalOnProperty(prefix="ai.customer-service",name="enabled",havingValue="true")
public class KnowledgePublicationAliasWorker {
    private static final Logger log=LoggerFactory.getLogger(KnowledgePublicationAliasWorker.class);
    private final KnowledgePublicationService publications;
    private final CustomerServiceAiClient client;
    private final CustomerServiceProperties properties;
    public KnowledgePublicationAliasWorker(KnowledgePublicationService publications,CustomerServiceAiClient client,
            CustomerServiceProperties properties) {
        this.publications=publications; this.client=client; this.properties=properties;
    }
    @Scheduled(fixedDelayString="${ai.customer-service.publication-alias-interval-millis:15000}")
    public void reconcile() {
        if(!properties.isPublicationEnabled()) return;
        try {
            var snapshot=publications.snapshot();
            // bootstrap 前没有权威镜像目标；不能把维护状态转成携带空目标的对账请求。
            if(!snapshot.enabled() || snapshot.publicationId()==null || snapshot.publicationId().isBlank()
                    || snapshot.collectionName()==null || snapshot.collectionName().isBlank()) return;
            client.reconcileAlias(new ReconcileAliasRequest(properties.getCollectionAlias(),snapshot.collectionName(),
                    snapshot.publicationId(),snapshot.epoch()));
            publications.acknowledgeMirror(snapshot);
        } catch(RuntimeException failure) {
            // 对账失败不撤销已提交发布，也不输出凭据、地址或供应商正文。
            log.warn("Knowledge alias reconciliation deferred: errorCode=KNOWLEDGE_ALIAS_RECONCILE_FAILED");
        }
    }
}
