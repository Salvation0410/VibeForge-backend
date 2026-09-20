package com.yupi.yuaicodemother.core.artifact;

import java.util.Map;

/** 返回已提交版本的标识和文件摘要。 */
public record ArtifactPublishResult(boolean published, String versionId, Map<String, String> hashes) { }
