package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.core.artifact.GenerationFenceService;
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
    private final GenerationFenceService generationFenceService;

    public enum PublicState {
        IDLE,
        RUNNING,
        STOPPING,
        COMMITTING
    }

    public record GenerationStateSnapshot(String requestId, PublicState state, boolean cancellationRequested) {
        public static GenerationStateSnapshot idle() {
            return new GenerationStateSnapshot(null, PublicState.IDLE, false);
        }
    }

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
        RLock transitionLock = transitionLock(appId);
        // 状态初始化必须与旧请求的提交/释放互斥；tryLock 保持忙时立即拒绝，不排队等待发布。
        if (!transitionLock.tryLock()) throw inProgress(requestId);
        try {
            // Redisson 允许同客户端同线程重入，但业务上每个应用只能运行一个请求。
            if (lock.isLocked() || !lock.tryLock()) throw inProgress(requestId);
            try {
                long fenceToken = generationFenceService.issue(appId, requestId);
                stateBucket(appId).set(state(requestId, ACTIVE));
                return new GenerationLease(appId, requestId, fenceToken, lock, ownerThreadId);
            } catch (RuntimeException error) {
                lock.unlockAsync(ownerThreadId);
                throw error;
            }
        } finally {
            transitionLock.unlock();
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
            if (!lease.lock().isLocked()) return false;
            if (!generationFenceService.cancel(lease.appId(), lease.requestId())) return false;
            mirrorCancellation(bucket, lease.appId(), lease.requestId());
            return true;
        } finally {
            transitionLock.unlock();
        }
    }

    /**
     * 按应用取消当前活动请求。重复取消保持 STOPPING；提交阶段不再允许取消胜出。
     */
    public GenerationStateSnapshot cancelActive(long appId) {
        RLock transitionLock = transitionLock(appId);
        transitionLock.lock();
        try {
            RBucket<String> bucket = stateBucket(appId);
            GenerationStateSnapshot current = liveSnapshot(appId, bucket.get());
            if (current.state() == PublicState.RUNNING) {
                if (!generationFenceService.cancel(appId, current.requestId())) {
                    return new GenerationStateSnapshot(current.requestId(), PublicState.COMMITTING, false);
                }
                mirrorCancellation(bucket, appId, current.requestId());
                return new GenerationStateSnapshot(current.requestId(), PublicState.STOPPING, true);
            }
            return current;
        } finally {
            transitionLock.unlock();
        }
    }

    /** 数据库取消已提交后，Redis 镜像失败不能阻止调用方通知引擎停止。 */
    private void mirrorCancellation(RBucket<String> bucket, long appId, String requestId) {
        try {
            bucket.set(state(requestId, CANCELLED));
        } catch (RuntimeException error) {
            log.warn("取消已持久化但实时镜像更新失败, appId={}, requestId={}, type={}",
                    appId, requestId, error.getClass().getSimpleName());
        }
    }

    public boolean isCancellationConfirmed(long appId, String requestId) {
        return generationFenceService.isCancelled(appId, requestId);
    }

    /** 返回应用当前对外生命周期状态，不暴露 Redis 内部状态名。 */
    public GenerationStateSnapshot getStatus(long appId) {
        RLock transitionLock = transitionLock(appId);
        transitionLock.lock();
        try {
            return liveSnapshot(appId, stateBucket(appId).get());
        } finally {
            transitionLock.unlock();
        }
    }

    /**
     * 先在 MySQL 持久化提交资格，再执行产物操作；Redis 只保存实时镜像。
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
            // ACTIVE/COMMITTING 只是状态记录；提交入口还必须有真实应用锁，失锁时失败关闭。
            // 此检查不是文件系统 fencing，不能保证检查后至发布完成期间锁永不丢失。
            if (!redissonClient.getLock("ai:generation:app:" + appId).isLocked()) {
                throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), "GENERATION_LEASE_LOST",
                        requestId, "生成租约已丢失，拒绝发布产物", null);
            }
            generationFenceService.beginCommit(appId, requestId);
            if (active) bucket.set(state(requestId, COMMITTING));
            // 提交资格一旦持久化不回退；动作失败或确认丢失不证明发布没有发生。
            T result = action.execute();
            try {
                bucket.set(state(requestId, COMMITTED));
            } catch (RuntimeException stateError) {
                // 产物已提交后状态标记失败只能降级记录，不能向上游报告失败并造成终态矛盾。
                log.warn("记录生成提交完成状态失败, appId={}, requestId={}", appId, requestId, stateError);
            }
            return result;
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

    private GenerationStreamException inProgress(String requestId) {
        return new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), "GENERATION_IN_PROGRESS",
                requestId, "当前应用正在生成，请稍后再试", null);
    }

    /** 残留状态键不是活动租约证明；只读隐藏失锁状态，不删除记录或推断任务终态。 */
    private GenerationStateSnapshot liveSnapshot(long appId, String value) {
        GenerationStateSnapshot current = parseSnapshot(value);
        if (current.state() != PublicState.IDLE
                && !redissonClient.getLock("ai:generation:app:" + appId).isLocked()) {
            return GenerationStateSnapshot.idle();
        }
        return current;
    }

    private GenerationStateSnapshot parseSnapshot(String value) {
        if (value == null || value.isBlank()) return GenerationStateSnapshot.idle();
        int separator = value.lastIndexOf(':');
        if (separator <= 0 || separator == value.length() - 1) {
            log.warn("忽略无法识别的生成状态: {}", value);
            return GenerationStateSnapshot.idle();
        }
        String requestId = value.substring(0, separator);
        String status = value.substring(separator + 1);
        return switch (status) {
            case ACTIVE -> new GenerationStateSnapshot(requestId, PublicState.RUNNING, false);
            case CANCELLED -> new GenerationStateSnapshot(requestId, PublicState.STOPPING, false);
            case COMMITTING, COMMITTED -> new GenerationStateSnapshot(requestId, PublicState.COMMITTING, false);
            default -> {
                log.warn("忽略未知生成状态: {}", status);
                yield GenerationStateSnapshot.idle();
            }
        };
    }

    /** 允许提交临界区执行可抛出受检异常的发布动作。 */
    @FunctionalInterface
    public interface CommitAction<T> {
        T execute() throws Exception;
    }
}
