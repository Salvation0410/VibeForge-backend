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
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

/** 真实 Redis 幂等验收，默认跳过；设置 AI_REDIS_INTEGRATION=true 后执行。 */
@EnabledIfEnvironmentVariable(named = "AI_REDIS_INTEGRATION", matches = "true")
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class ToolInvocationIdempotencyRedisIT {
    private RedissonClient firstClient;
    private RedissonClient secondClient;
    private ToolInvocationIdempotencyService first;
    private ToolInvocationIdempotencyService second;

    @BeforeAll
    void connect() {
        String address = System.getenv().getOrDefault("AI_REDIS_URL", "redis://127.0.0.1:6379/1");
        firstClient = Redisson.create(config(address));
        secondClient = Redisson.create(config(address));
        AiEngineProperties properties = new AiEngineProperties();
        properties.setToolIdempotencyTtlSeconds(60);
        properties.setToolIdempotencyLockWaitMillis(100);
        first = new ToolInvocationIdempotencyService(firstClient, new ObjectMapper(), properties);
        second = new ToolInvocationIdempotencyService(secondClient, new ObjectMapper(), properties);
    }

    @AfterAll
    void close() {
        if (firstClient != null) firstClient.shutdown();
        if (secondClient != null) secondClient.shutdown();
    }

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

    private Config config(String address) {
        Config config = new Config();
        config.useSingleServer().setAddress(address);
        return config;
    }

    private String unique(String prefix) { return prefix + "-" + UUID.randomUUID(); }

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
