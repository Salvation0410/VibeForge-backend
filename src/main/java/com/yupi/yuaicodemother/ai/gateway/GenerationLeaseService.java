package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.exception.ErrorCode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RBucket;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Service;

import java.util.Objects;

/** 使用 Redis 租约阻止同一应用同时运行多个生成任务。 */
@Service
@RequiredArgsConstructor
@Slf4j
public class GenerationLeaseService {
    private static final String ACTIVE = "ACTIVE";
    private static final String CANCELLED = "CANCELLED";
    private static final String COMMITTING = "COMMITTING";
    private static final String COMMITTED = "COMMITTED";
    private final RedissonClient redissonClient;

    /**
     * 立即尝试获取应用级生成租约，不排队等待已有任务。
     *
     * @param appId 应用 ID
     * @param requestId 当前请求 ID
     * @return 包含锁所有者信息的租约
     * @throws GenerationStreamException 同一应用已有生成任务时携带稳定错误码抛出
     */
    public GenerationLease acquire(long appId, String requestId) {
        RLock lock = redissonClient.getLock("ai:generation:app:" + appId);
        long ownerThreadId = Thread.currentThread().threadId();
        if (!lock.tryLock()) {
            throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), "GENERATION_IN_PROGRESS",
                    requestId, "当前应用正在生成，请稍后再试", null);
        }
        try {
            stateBucket(appId).set(state(requestId, ACTIVE));
            return new GenerationLease(appId, requestId, lock, ownerThreadId);
        } catch (RuntimeException error) {
            lock.unlockAsync(ownerThreadId);
            throw error;
        }
    }

    /**
     * 原子标记取消，仅 ACTIVE 可以转为 CANCELLED；提交已开始或完成时取消不再胜出。
     *
     * @return true 表示应继续通知下游引擎取消，false 表示请求已经提交或不再活动
     */
    public boolean cancel(GenerationLease lease) {
        if (lease == null) return false;
        RLock transitionLock = transitionLock(lease.appId());
        transitionLock.lock();
        try {
            RBucket<String> bucket = stateBucket(lease.appId());
            if (!Objects.equals(bucket.get(), state(lease.requestId(), ACTIVE))) return false;
            bucket.set(state(lease.requestId(), CANCELLED));
            return true;
        } finally {
            transitionLock.unlock();
        }
    }

    /**
     * 在与取消互斥的临界区提交产物；先转为 COMMITTING 使提交胜出，完成标记失败不反向否定已发布版本。
     *
     * @throws GenerationStreamException 请求已取消或不再是当前活动请求时抛出
     */
    public <T> T commit(long appId, String requestId, CommitAction<T> action) throws Exception {
        RLock transitionLock = transitionLock(appId);
        transitionLock.lock();
        try {
            RBucket<String> bucket = stateBucket(appId);
            String current = bucket.get();
            boolean active = Objects.equals(current, state(requestId, ACTIVE));
            boolean committed = Objects.equals(current, state(requestId, COMMITTED));
            boolean committing = Objects.equals(current, state(requestId, COMMITTING));
            if (!active && !committed && !committing) {
                String errorCode = Objects.equals(current, state(requestId, CANCELLED))
                        ? "GENERATION_CANCELLED" : "GENERATION_NOT_ACTIVE";
                throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), errorCode, requestId,
                        "生成请求已取消或不再活动，拒绝发布产物", null);
            }
            if (active) bucket.set(state(requestId, COMMITTING));
            try {
                T result = action.execute();
                try {
                    bucket.set(state(requestId, COMMITTED));
                } catch (RuntimeException stateError) {
                    // 产物已提交后状态标记失败只能降级记录，不能向上游报告失败并造成终态矛盾。
                    log.warn("记录生成提交完成状态失败, appId={}, requestId={}", appId, requestId, stateError);
                }
                return result;
            } catch (Exception | Error publishError) {
                if (active) {
                    try { bucket.set(state(requestId, ACTIVE)); }
                    catch (RuntimeException stateError) {
                        publishError.addSuppressed(stateError);
                    }
                }
                throw publishError;
            }
        } finally {
            transitionLock.unlock();
        }
    }

    /**
     * 按原所有者线程释放租约；锁已释放或已过期时不影响终止链路。
     *
     * @param lease 当前请求持有的租约
     */
    public void release(GenerationLease lease) {
        if (lease == null) return;
        RLock transitionLock = transitionLock(lease.appId());
        transitionLock.lock();
        try {
            RBucket<String> bucket = stateBucket(lease.appId());
            String current = bucket.get();
            if (current != null && current.startsWith(lease.requestId() + ":")) bucket.delete();
        } finally {
            transitionLock.unlock();
            lease.lock().unlockAsync(lease.ownerThreadId());
        }
    }

    /** 返回应用生成提交状态使用的 Redis 桶。 */
    private RBucket<String> stateBucket(long appId) {
        return redissonClient.getBucket("ai:generation:state:" + appId);
    }

    /** 返回串行化取消与提交状态转换的分布式锁。 */
    private RLock transitionLock(long appId) {
        return redissonClient.getLock("ai:generation:transition:" + appId);
    }

    /** 组合请求 ID 与生命周期状态，防止旧请求操作新请求状态。 */
    private String state(String requestId, String status) {
        return requestId + ":" + status;
    }

    /** 允许提交临界区执行可抛出受检异常的发布动作。 */
    @FunctionalInterface
    public interface CommitAction<T> {
        T execute() throws Exception;
    }
}
