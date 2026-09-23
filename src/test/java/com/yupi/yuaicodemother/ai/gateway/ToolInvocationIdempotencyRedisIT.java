package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.redisson.Redisson;
import org.redisson.api.RedissonClient;
import org.redisson.config.Config;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** 真实 Redis 幂等验收，默认跳过；设置 AI_REDIS_INTEGRATION=true 后执行。 */
@EnabledIfEnvironmentVariable(named = "AI_REDIS_INTEGRATION", matches = "true")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ToolInvocationIdempotencyRedisIT {
    private static final long LOCK_WATCHDOG_MILLIS = 1_000L;

    private String redisAddress;
    private RedissonClient firstClient;
    private RedissonClient secondClient;
    private ToolInvocationIdempotencyService first;
    private ToolInvocationIdempotencyService second;

    /** 按环境变量连接两个独立 Redis 客户端，模拟不同 Spring 实例。 */
    @BeforeAll
    void connect() {
        redisAddress = System.getenv().getOrDefault("AI_REDIS_URL", "redis://127.0.0.1:6379/1");
        firstClient = Redisson.create(config(redisAddress));
        secondClient = Redisson.create(config(redisAddress));
        AiEngineProperties properties = new AiEngineProperties();
        properties.setToolIdempotencyTtlSeconds(60);
        properties.setToolIdempotencyLockWaitMillis(100);
        first = new ToolInvocationIdempotencyService(firstClient, new ObjectMapper(), properties);
        second = new ToolInvocationIdempotencyService(secondClient, new ObjectMapper(), properties);
    }

    /** 测试结束后释放全部共享客户端连接。 */
    @AfterAll
    void close() {
        if (firstClient != null) firstClient.shutdown();
        if (secondClient != null) secondClient.shutdown();
    }

    /** 验证不同 Redis 客户端能回放同一作用域的成功结果，且不会重复执行工具。 */
    @Test
    void replaysSuccessfulResultAcrossTwoRedisClients() {
        String requestId = unique("replay");
        String toolCallId = unique("call");
        AtomicInteger executions = new AtomicInteger();
        Map<String, Object> firstResult = first.execute(42L, requestId, toolCallId,
                InternalAiTool.FILE_READ, Map.of("relativeFilePath", "src/App.vue"),
                () -> { executions.incrementAndGet(); return Map.of("content", "ready"); });
        Map<String, Object> replay = second.execute(42L, requestId, toolCallId,
                InternalAiTool.FILE_READ, Map.of("relativeFilePath", "src/App.vue"),
                () -> { executions.incrementAndGet(); return Map.of("content", "wrong"); });
        assertEquals(firstResult, replay);
        assertEquals(1, executions.get());
    }

    /** 验证遗留 RUNNING 状态按不确定结果处理，重试不得再次执行工具。 */
    @Test
    void staleRunningStateIsIndeterminateAndDoesNotExecuteAction() {
        String requestId = unique("running");
        String toolCallId = unique("call");
        String key = "ai:tool:idempotency:v1:42:" + requestId + ":" + toolCallId;
        String fingerprint = fingerprint("file_read", "{\"relativeFilePath\":\"src/App.vue\"}");
        String state = "{\"status\":\"RUNNING\",\"toolName\":\"file_read\","
                + "\"requestFingerprint\":\"" + fingerprint + "\",\"result\":null,"
                + "\"startedAtEpochMillis\":1,\"completedAtEpochMillis\":null}";
        firstClient.getBucket(key).set(state, 60, TimeUnit.SECONDS);
        AtomicInteger executions = new AtomicInteger();
        BusinessException error = assertThrows(BusinessException.class, () -> second.execute(
                42L, requestId, toolCallId, InternalAiTool.FILE_READ,
                Map.of("relativeFilePath", "src/App.vue"),
                () -> { executions.incrementAndGet(); return Map.of(); }));
        assertEquals("TOOL_EXECUTION_INDETERMINATE", error.getMessage());
        assertEquals(0, executions.get());
    }

    /** 验证 action 成功后断开 Redis 会保留 RUNNING，连接恢复后的重试仍不得重复执行。 */
    @Test
    void successfulActionWithLostRedisConnectionRemainsIndeterminate() throws Exception {
        String requestId = unique("writeback");
        String toolCallId = unique("call");
        String stateKey = "ai:tool:idempotency:v1:42:" + requestId + ":" + toolCallId;
        AtomicInteger executions = new AtomicInteger();
        CountDownLatch actionStarted = new CountDownLatch(1);
        CountDownLatch releaseAction = new CountDownLatch(1);
        ExecutorService executor = Executors.newSingleThreadExecutor();
        RedissonClient actionClient = Redisson.create(config(redisAddress));
        RedissonClient retryClient = null;

        try {
            AiEngineProperties properties = properties();
            ToolInvocationIdempotencyService actionService = new ToolInvocationIdempotencyService(
                    actionClient, new ObjectMapper(), properties);
            Future<Map<String, Object>> execution = executor.submit(() -> actionService.execute(
                    42L, requestId, toolCallId, InternalAiTool.FILE_WRITE,
                    Map.of("relativeFilePath", "src/App.vue", "content", "ready"),
                    () -> {
                        executions.incrementAndGet();
                        actionStarted.countDown();
                        try {
                            if (!releaseAction.await(10, TimeUnit.SECONDS)) {
                                throw new IllegalStateException("Timed out waiting to simulate Redis disconnect");
                            }
                        } catch (InterruptedException error) {
                            Thread.currentThread().interrupt();
                            throw new IllegalStateException("Interrupted while simulating Redis disconnect", error);
                        }
                        return Map.of("ok", true);
                    }));

            assertTrue(actionStarted.await(10, TimeUnit.SECONDS), "tool action did not start");
            // 精确在 action 完成前关闭客户端，使后续 SUCCEEDED 状态写入真实失败。
            actionClient.shutdown();
            releaseAction.countDown();

            ExecutionException failure = assertThrows(
                    ExecutionException.class,
                    () -> execution.get(10, TimeUnit.SECONDS));
            assertTrue(failure.getCause() instanceof BusinessException);
            assertEquals("TOOL_EXECUTION_INDETERMINATE", failure.getCause().getMessage());

            retryClient = Redisson.create(config(redisAddress));
            String persistedState = (String) retryClient.getBucket(stateKey).get();
            assertTrue(persistedState != null && persistedState.contains("\"status\":\"RUNNING\""),
                    "RUNNING state was not preserved after the Redis client disconnected");
            // 模拟进程退出后的锁租约到期，再验证同一作用域的重试语义。
            awaitLockExpiry(retryClient, stateKey + ":lock");

            ToolInvocationIdempotencyService retryService = new ToolInvocationIdempotencyService(
                    retryClient, new ObjectMapper(), properties);
            BusinessException replay = assertThrows(BusinessException.class, () -> retryService.execute(
                    42L, requestId, toolCallId, InternalAiTool.FILE_WRITE,
                    Map.of("relativeFilePath", "src/App.vue", "content", "ready"),
                    () -> {
                        executions.incrementAndGet();
                        return Map.of("ok", true);
                    }));
            assertEquals("TOOL_EXECUTION_INDETERMINATE", replay.getMessage());
            assertEquals(1, executions.get());
        } finally {
            releaseAction.countDown();
            executor.shutdownNow();
            if (!actionClient.isShutdown()) actionClient.shutdown();
            if (retryClient != null) retryClient.shutdown();
        }
    }

    /** 创建隔离测试使用的 Redisson 配置，并缩短故障场景的锁租约。 */
    private Config config(String address) {
        Config config = new Config();
        // 缩短仅用于测试的锁看门狗时间，避免进程中止场景等待默认 30 秒。
        config.setLockWatchdogTimeout(LOCK_WATCHDOG_MILLIS);
        config.useSingleServer().setAddress(address);
        return config;
    }

    /** 创建与集成场景一致的幂等过期和锁等待配置。 */
    private AiEngineProperties properties() {
        AiEngineProperties properties = new AiEngineProperties();
        properties.setToolIdempotencyTtlSeconds(60);
        properties.setToolIdempotencyLockWaitMillis(100);
        return properties;
    }

    /** 有界等待已断开客户端持有的 Redis 锁自然过期。 */
    private void awaitLockExpiry(RedissonClient client, String lockKey) throws InterruptedException {
        long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(5);
        while (client.getLock(lockKey).isLocked() && System.nanoTime() < deadline) {
            Thread.sleep(50);
        }
        assertTrue(!client.getLock(lockKey).isLocked(), "Redis lock did not expire after client shutdown");
    }

    /** 为每个真实 Redis 场景生成互不冲突的作用域标识。 */
    private String unique(String prefix) { return prefix + "-" + UUID.randomUUID(); }

    /** 按生产实现的规范化文本计算请求指纹，用于手工写入 RUNNING 状态。 */
    private String fingerprint(String toolName, String arguments) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest((toolName + "\n" + arguments).getBytes(StandardCharsets.UTF_8));
            return java.util.HexFormat.of().formatHex(digest);
        } catch (Exception error) {
            throw new IllegalStateException(error);
        }
    }
}
