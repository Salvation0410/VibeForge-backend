package com.yupi.yuaicodemother.ai.gateway;

import org.redisson.api.RLock;

/** 保存一次应用生成租约；ownerThreadId 是 Redisson 逻辑所有者标识，不是 Java 工作线程 ID。 */
public record GenerationLease(long appId, String requestId, long fenceToken, RLock lock, long ownerThreadId) { }
