package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.core.JsonGenerator;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonSerializer;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializerProvider;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.module.SimpleModule;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import lombok.extern.slf4j.Slf4j;
import org.redisson.api.RBucket;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.TimeUnit;
import java.util.regex.Pattern;

/** 使用 Redis 在 Spring 实例之间共享内部工具调用状态和成功结果。 */
@Service
@Slf4j
public class ToolInvocationIdempotencyService {
    private static final Pattern SCOPE_ID_PATTERN = Pattern.compile("[A-Za-z0-9:_-]{1,200}");
    private static final String KEY_PREFIX = "ai:tool:idempotency:v1:";
    private static final String RUNNING = "RUNNING";
    private static final String SUCCEEDED = "SUCCEEDED";

    private final RedissonClient redissonClient;
    private final ObjectMapper objectMapper;
    private final AiEngineProperties properties;

    public ToolInvocationIdempotencyService(
            RedissonClient redissonClient,
            ObjectMapper objectMapper,
            AiEngineProperties properties) {
        this.redissonClient = redissonClient;
        ObjectMapper idempotencyMapper = objectMapper.copy()
                .deactivateDefaultTyping()
                .configure(SerializationFeature.ORDER_MAP_ENTRIES_BY_KEYS, true);
        SimpleModule numericLongModule = new SimpleModule("tool-idempotency-numeric-long");
        JsonSerializer<Long> numericLongSerializer = new JsonSerializer<>() {
            @Override
            public void serialize(Long value, JsonGenerator generator, SerializerProvider serializers)
                    throws IOException {
                generator.writeNumber(value);
            }
        };
        numericLongModule.addSerializer(Long.class, numericLongSerializer);
        numericLongModule.addSerializer(long.class, numericLongSerializer);
        idempotencyMapper.registerModule(numericLongModule);
        this.objectMapper = idempotencyMapper;
        this.properties = properties;
    }

    public Map<String, Object> execute(
            long appId,
            String requestId,
            String toolCallId,
            InternalAiTool tool,
            Map<String, Object> arguments,
            ToolAction action) {
        validateScopeId(requestId, "requestId");
        validateScopeId(toolCallId, "toolCallId");
        if (tool == null || arguments == null || action == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "INVALID_TOOL_IDEMPOTENCY_REQUEST");
        }

        String toolName = tool.canonicalName();
        String fingerprint = fingerprint(toolName, arguments);
        String key = KEY_PREFIX + appId + ":" + encodeKeyComponent(requestId)
                + ":" + encodeKeyComponent(toolCallId);
        RLock lock;
        try {
            lock = redissonClient.getLock(key + ":lock");
        } catch (RuntimeException error) {
            throw unavailable(error);
        }

        boolean locked = false;
        try {
            try {
                locked = lock.tryLock(properties.getToolIdempotencyLockWaitMillis(), TimeUnit.MILLISECONDS);
            } catch (InterruptedException error) {
                Thread.currentThread().interrupt();
                throw busy(error);
            } catch (RuntimeException error) {
                throw unavailable(error);
            }
            if (!locked) throw busy(null);

            RBucket<String> bucket;
            IdempotencyState existing;
            try {
                bucket = redissonClient.getBucket(key);
                existing = readState(bucket.get());
            } catch (RuntimeException error) {
                throw unavailable(error);
            }

            if (existing != null) {
                if (!Objects.equals(toolName, existing.toolName())
                        || !Objects.equals(fingerprint, existing.requestFingerprint())) {
                    throw operationError("TOOL_IDEMPOTENCY_CONFLICT");
                }
                if (RUNNING.equals(existing.status())) {
                    throw operationError("TOOL_EXECUTION_INDETERMINATE");
                }
                if (SUCCEEDED.equals(existing.status())) return existing.result();
                throw unavailable(null);
            }

            long startedAt = System.currentTimeMillis();
            IdempotencyState running = new IdempotencyState(
                    RUNNING, toolName, fingerprint, null, startedAt, null);
            try {
                writeState(bucket, running);
            } catch (RuntimeException error) {
                throw unavailable(error);
            }

            Map<String, Object> result;
            try {
                result = action.execute();
            } catch (RuntimeException | Error actionError) {
                try {
                    bucket.delete();
                } catch (RuntimeException cleanupError) {
                    actionError.addSuppressed(cleanupError);
                }
                throw actionError;
            }

            IdempotencyState succeeded = new IdempotencyState(
                    SUCCEEDED, toolName, fingerprint, result, startedAt, System.currentTimeMillis());
            String succeededJson;
            IdempotencyState normalizedSucceeded;
            try {
                succeededJson = serializeState(succeeded);
                normalizedSucceeded = readState(succeededJson);
            } catch (RuntimeException error) {
                throw indeterminate(error);
            }
            try {
                writeState(bucket, succeededJson);
            } catch (RuntimeException error) {
                throw indeterminate(error);
            }
            return normalizedSucceeded.result();
        } finally {
            if (locked) releaseLock(lock, key);
        }
    }

    private void releaseLock(RLock lock, String key) {
        try {
            if (lock.isHeldByCurrentThread()) lock.unlock();
        } catch (RuntimeException error) {
            // 解锁失败不能覆盖已经确定的工具结果或原始 action 异常，保留告警供运维排查。
            log.warn("释放工具幂等锁失败, key={}", key, error);
        }
    }

    private void validateScopeId(String value, String field) {
        if (value == null || !SCOPE_ID_PATTERN.matcher(value).matches()) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "INVALID_" + field.toUpperCase());
        }
    }

    private String encodeKeyComponent(String value) {
        return value.replace("%", "%25").replace(":", "%3A");
    }

    private String fingerprint(String toolName, Map<String, Object> arguments) {
        try {
            String canonicalArguments = objectMapper.writeValueAsString(arguments);
            byte[] digest = MessageDigest.getInstance("SHA-256")
                    .digest((toolName + "\n" + canonicalArguments).getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (JsonProcessingException | NoSuchAlgorithmException error) {
            throw unavailable(error);
        }
    }

    private IdempotencyState readState(String json) {
        if (json == null) return null;
        try {
            return objectMapper.readValue(json, IdempotencyState.class);
        } catch (JsonProcessingException error) {
            throw unavailable(error);
        }
    }

    private void writeState(RBucket<String> bucket, IdempotencyState state) {
        writeState(bucket, serializeState(state));
    }

    private void writeState(RBucket<String> bucket, String json) {
        bucket.set(json, properties.getToolIdempotencyTtlSeconds(), TimeUnit.SECONDS);
    }

    private String serializeState(IdempotencyState state) {
        try {
            return objectMapper.writeValueAsString(state);
        } catch (JsonProcessingException error) {
            throw unavailable(error);
        }
    }

    private BusinessException unavailable(Throwable cause) {
        return businessError("TOOL_IDEMPOTENCY_UNAVAILABLE", cause);
    }

    private BusinessException busy(Throwable cause) {
        return businessError("TOOL_EXECUTION_BUSY", cause);
    }

    private BusinessException indeterminate(Throwable cause) {
        return businessError("TOOL_EXECUTION_INDETERMINATE", cause);
    }

    private BusinessException operationError(String message) {
        return new BusinessException(ErrorCode.OPERATION_ERROR, message);
    }

    private BusinessException businessError(String message, Throwable cause) {
        BusinessException error = operationError(message);
        if (cause != null) error.initCause(cause);
        return error;
    }

    private record IdempotencyState(
            String status,
            String toolName,
            String requestFingerprint,
            Map<String, Object> result,
            long startedAtEpochMillis,
            Long completedAtEpochMillis) {
    }

    @FunctionalInterface
    public interface ToolAction {
        Map<String, Object> execute();
    }
}
