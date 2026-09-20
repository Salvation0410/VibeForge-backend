package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import lombok.RequiredArgsConstructor;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Service;

/** 使用 Redis 租约阻止同一应用同时运行多个生成任务。 */
@Service
@RequiredArgsConstructor
public class GenerationLeaseService {
    private final RedissonClient redissonClient;

    /**
     * 立即尝试获取应用级生成租约，不排队等待已有任务。
     *
     * @param appId 应用 ID
     * @param requestId 当前请求 ID
     * @return 包含锁所有者信息的租约
     * @throws BusinessException 同一应用已有生成任务时抛出
     */
    public GenerationLease acquire(long appId, String requestId) {
        RLock lock = redissonClient.getLock("ai:generation:app:" + appId);
        long ownerThreadId = Thread.currentThread().threadId();
        if (!lock.tryLock()) {
            throw new BusinessException(ErrorCode.OPERATION_ERROR, "GENERATION_IN_PROGRESS: 当前应用正在生成，请稍后再试");
        }
        return new GenerationLease(appId, requestId, lock, ownerThreadId);
    }

    /**
     * 按原所有者线程释放租约；锁已释放或已过期时不影响终止链路。
     *
     * @param lease 当前请求持有的租约
     */
    public void release(GenerationLease lease) {
        if (lease == null) return;
        lease.lock().unlockAsync(lease.ownerThreadId());
    }
}
