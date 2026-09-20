package com.yupi.yuaicodemother.ai.gateway;

import org.redisson.api.RLock;

/** 保存一次应用生成租约及其 Redisson 所有者线程。 */
public record GenerationLease(long appId, String requestId, RLock lock, long ownerThreadId) { }
