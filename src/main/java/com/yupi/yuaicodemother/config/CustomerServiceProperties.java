package com.yupi.yuaicodemother.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;

@Data
@ConfigurationProperties(prefix = "ai.customer-service")
public class CustomerServiceProperties {
    private boolean enabled = false;
    private String serviceUrl = "http://localhost:8000";
    private long timeoutSeconds = 30;
    private long pollIntervalMillis = 5000;
    private int batchSize = 10;
    private long claimTimeoutSeconds = 60;
    private long leaseTimeoutSeconds = 45;
    private int retryMax = 5;
    private long retryBaseDelaySeconds = 5;
    private int maxResponseBytes = 65536;
}
