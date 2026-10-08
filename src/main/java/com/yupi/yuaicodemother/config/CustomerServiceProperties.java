package com.yupi.yuaicodemother.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import jakarta.annotation.PostConstruct;

@Data
@ConfigurationProperties(prefix = "ai.customer-service")
public class CustomerServiceProperties {
    private boolean enabled = false;
    private String serviceUrl = "http://localhost:8000";
    private long timeoutSeconds = 30;
    private String collectionAlias = "customer_service_knowledge";
    private long requestPreparationMarginSeconds = 10;
    private long pollIntervalMillis = 5000;
    private int batchSize = 10;
    private long claimTimeoutSeconds = 60;
    private long leaseTimeoutSeconds = 45;
    private int retryMax = 5;
    private long retryBaseDelaySeconds = 5;
    private int maxResponseBytes = 65536;
    private int rebuildMaxDocuments = 1000;
    private int rebuildMaxRequestBytes = 4_194_304;

    @PostConstruct
    public void validate() {
        if (!enabled) return;
        if (serviceUrl == null || !serviceUrl.matches("https?://[^\\s/]+(?::\\d+)?(?:/.*)?"))
            throw new IllegalStateException("AI_CUSTOMER_SERVICE_SERVICE_URL is invalid");
        // Python 允许 alias 最多 255 个字符，但包含 "collection:" 前缀后租约作用域上限为 256 个字符。
        if (collectionAlias == null || !collectionAlias.matches("[A-Za-z_][A-Za-z0-9_]{0,244}"))
            throw new IllegalStateException("AI_CUSTOMER_SERVICE_COLLECTION_ALIAS is invalid");
        if (timeoutSeconds < 1 || timeoutSeconds > 300
                || requestPreparationMarginSeconds < 1 || requestPreparationMarginSeconds > 300
                || claimTimeoutSeconds > 3600 || leaseTimeoutSeconds > 3600
                || claimTimeoutSeconds <= timeoutSeconds + requestPreparationMarginSeconds
                || leaseTimeoutSeconds <= timeoutSeconds + requestPreparationMarginSeconds)
            throw new IllegalStateException("Customer-service claim and lease timeouts must cover preparation plus Python timeout");
        if (pollIntervalMillis < 1 || batchSize < 1 || batchSize > 100 || retryMax < 1 || retryMax > 20
                || retryBaseDelaySeconds < 1 || retryBaseDelaySeconds > 3600
                || maxResponseBytes < 1024 || maxResponseBytes > 1_048_576
                || rebuildMaxDocuments < 1 || rebuildMaxDocuments > 10_000
                || rebuildMaxRequestBytes < 1024 || rebuildMaxRequestBytes > 16_777_216)
            throw new IllegalStateException("Customer-service worker configuration is invalid");
    }
}
