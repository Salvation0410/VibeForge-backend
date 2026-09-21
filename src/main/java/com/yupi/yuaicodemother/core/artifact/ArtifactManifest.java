package com.yupi.yuaicodemother.core.artifact;

import java.time.Instant;
import java.util.Map;

/** 记录不可变产物版本的来源和文件摘要。 */
public record ArtifactManifest(String requestId, long appId, String engine, Instant createdAt,
                               long sequence, Map<String, String> hashes, String finishReason,
                               int validatorVersion) { }
