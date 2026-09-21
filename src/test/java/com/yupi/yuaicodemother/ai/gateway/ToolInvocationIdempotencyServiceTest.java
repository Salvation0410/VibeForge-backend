package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import org.junit.jupiter.api.Test;
import org.redisson.api.RBucket;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;

import java.util.Map;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertSame;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class ToolInvocationIdempotencyServiceTest {

    @Test
    void replaysMatchingSuccessfulResultWithoutExecutingAgain() {
        Fixture fixture = fixture();
        AtomicInteger executions = new AtomicInteger();

        Map<String, Object> first = fixture.service.execute(42L, "req-1", "call-1",
                InternalAiTool.FILE_READ, Map.of("relativeFilePath", "src/App.vue"),
                () -> { executions.incrementAndGet(); return Map.of("content", "ready"); });
        Map<String, Object> replay = fixture.service.execute(42L, "req-1", "call-1",
                InternalAiTool.FILE_READ, Map.of("relativeFilePath", "src/App.vue"),
                () -> { executions.incrementAndGet(); return Map.of("content", "wrong"); });

        assertEquals(first, replay);
        assertEquals(1, executions.get());
        verify(fixture.client, times(2)).getBucket("ai:tool:idempotency:v1:42:req-1:call-1");
        verify(fixture.client, times(2)).getLock("ai:tool:idempotency:v1:42:req-1:call-1:lock");
        verify(fixture.bucket, times(2)).set(anyString(), eq(86400L), eq(TimeUnit.SECONDS));
        assertTrue(fixture.value.get().contains("\"status\":\"SUCCEEDED\""));
        assertTrue(fixture.value.get().contains("\"completedAtEpochMillis\":"));
    }

    @Test
    void rejectsReusedScopeWithDifferentPayload() {
        Fixture fixture = fixture();
        fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                Map.of("relativeFilePath", "src/App.vue"), () -> Map.of("content", "ready"));

        BusinessException error = assertThrows(BusinessException.class,
                () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                        Map.of("relativeFilePath", "src/main.ts"), () -> Map.of("content", "wrong")));

        assertEquals("TOOL_IDEMPOTENCY_CONFLICT", error.getMessage());
    }

    @Test
    void rejectsReusedScopeWithDifferentCanonicalTool() {
        Fixture fixture = fixture();
        fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                Map.of("relativeFilePath", "src/App.vue"), () -> Map.of("content", "ready"));

        BusinessException error = assertThrows(BusinessException.class,
                () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_WRITE,
                        Map.of("relativeFilePath", "src/App.vue"), () -> Map.of("ok", true)));

        assertEquals("TOOL_IDEMPOTENCY_CONFLICT", error.getMessage());
    }

    @Test
    void rejectsIndeterminateRunningStateWithoutExecutingAgain() {
        Fixture fixture = fixture();
        AtomicInteger executions = new AtomicInteger();
        AtomicInteger writes = new AtomicInteger();
        doAnswer(invocation -> {
            if (writes.incrementAndGet() == 2) throw new IllegalStateException("redis unavailable");
            fixture.value.set(invocation.getArgument(0));
            return null;
        }).when(fixture.bucket).set(anyString(), anyLong(), eq(TimeUnit.SECONDS));

        BusinessException first = assertThrows(BusinessException.class,
                () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                        Map.of("relativeFilePath", "src/App.vue"),
                        () -> { executions.incrementAndGet(); return Map.of("content", "ready"); }));

        BusinessException replay = assertThrows(BusinessException.class,
                () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                        Map.of("relativeFilePath", "src/App.vue"),
                        () -> { executions.incrementAndGet(); return Map.of(); }));

        assertEquals("TOOL_EXECUTION_INDETERMINATE", first.getMessage());
        assertEquals("TOOL_EXECUTION_INDETERMINATE", replay.getMessage());
        assertEquals(1, executions.get());
    }

    @Test
    void rejectsWhenScopedLockCannotBeAcquired() throws Exception {
        Fixture fixture = fixture();
        when(fixture.lock.tryLock(30000L, TimeUnit.MILLISECONDS)).thenReturn(false);

        BusinessException error = assertThrows(BusinessException.class,
                () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                        Map.of("relativeFilePath", "src/App.vue"), Map::of));

        assertEquals("TOOL_EXECUTION_BUSY", error.getMessage());
        verify(fixture.bucket, never()).set(anyString(), anyLong(), eq(TimeUnit.SECONDS));
    }

    @Test
    void restoresInterruptFlagWhenLockWaitIsInterrupted() throws Exception {
        Fixture fixture = fixture();
        when(fixture.lock.tryLock(30000L, TimeUnit.MILLISECONDS)).thenThrow(new InterruptedException());

        try {
            BusinessException error = assertThrows(BusinessException.class,
                    () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                            Map.of("relativeFilePath", "src/App.vue"), Map::of));

            assertEquals("TOOL_EXECUTION_BUSY", error.getMessage());
            assertTrue(Thread.currentThread().isInterrupted());
            verify(fixture.bucket, never()).set(anyString(), anyLong(), eq(TimeUnit.SECONDS));
        } finally {
            Thread.interrupted();
        }
    }

    @Test
    void deletesRunningClaimWhenActionFailsExplicitly() {
        Fixture fixture = fixture();
        IllegalStateException expected = new IllegalStateException("tool failed");

        IllegalStateException actual = assertThrows(IllegalStateException.class,
                () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_WRITE,
                        Map.of("relativeFilePath", "src/App.vue", "content", "ready"),
                        () -> { throw expected; }));

        assertSame(expected, actual);
        assertEquals(null, fixture.value.get());
        verify(fixture.bucket).delete();
    }

    @Test
    void unlockFailureDoesNotMaskOriginalActionFailure() {
        Fixture fixture = fixture();
        IllegalStateException expected = new IllegalStateException("tool failed");
        when(fixture.lock.isHeldByCurrentThread()).thenThrow(new IllegalStateException("redis unavailable"));

        IllegalStateException actual = assertThrows(IllegalStateException.class,
                () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_WRITE,
                        Map.of(), () -> { throw expected; }));

        assertSame(expected, actual);
    }

    @Test
    void unlockFailureDoesNotMaskStoredSuccessfulResult() {
        Fixture fixture = fixture();
        when(fixture.lock.isHeldByCurrentThread()).thenThrow(new IllegalStateException("redis unavailable"));

        Map<String, Object> result = fixture.service.execute(
                42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                Map.of(), () -> Map.of("content", "ready"));

        assertEquals(Map.of("content", "ready"), result);
        assertTrue(fixture.value.get().contains("\"status\":\"SUCCEEDED\""));
    }

    @Test
    void preservesRunningWhenSuccessfulActionCannotStoreResult() {
        Fixture fixture = fixture();
        AtomicInteger writes = new AtomicInteger();
        doAnswer(invocation -> {
            if (writes.incrementAndGet() == 2) throw new IllegalStateException("redis unavailable");
            fixture.value.set(invocation.getArgument(0));
            return null;
        }).when(fixture.bucket).set(anyString(), anyLong(), eq(TimeUnit.SECONDS));

        BusinessException error = assertThrows(BusinessException.class,
                () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_WRITE,
                        Map.of("relativeFilePath", "src/App.vue", "content", "ready"),
                        () -> Map.of("ok", true)));

        assertEquals("TOOL_EXECUTION_INDETERMINATE", error.getMessage());
        assertTrue(fixture.value.get().contains("RUNNING"));
        verify(fixture.bucket, never()).delete();
    }

    @Test
    void failsClosedWhenRunningClaimCannotBeStored() {
        Fixture fixture = fixture();
        doThrow(new IllegalStateException("redis unavailable"))
                .when(fixture.bucket).set(anyString(), anyLong(), eq(TimeUnit.SECONDS));
        AtomicInteger executions = new AtomicInteger();

        BusinessException error = assertThrows(BusinessException.class,
                () -> fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                        Map.of("relativeFilePath", "src/App.vue"),
                        () -> { executions.incrementAndGet(); return Map.of(); }));

        assertEquals("TOOL_IDEMPOTENCY_UNAVAILABLE", error.getMessage());
        assertEquals(0, executions.get());
    }

    @Test
    void failsClosedWhenLockOrStateReadFails() throws Exception {
        Fixture lockFailure = fixture();
        when(lockFailure.lock.tryLock(30000L, TimeUnit.MILLISECONDS))
                .thenThrow(new IllegalStateException("redis unavailable"));
        AtomicInteger lockExecutions = new AtomicInteger();

        BusinessException lockError = assertThrows(BusinessException.class,
                () -> lockFailure.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                        Map.of(), () -> { lockExecutions.incrementAndGet(); return Map.of(); }));

        Fixture readFailure = fixture();
        when(readFailure.bucket.get()).thenThrow(new IllegalStateException("redis unavailable"));
        AtomicInteger readExecutions = new AtomicInteger();
        BusinessException readError = assertThrows(BusinessException.class,
                () -> readFailure.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                        Map.of(), () -> { readExecutions.incrementAndGet(); return Map.of(); }));

        assertEquals("TOOL_IDEMPOTENCY_UNAVAILABLE", lockError.getMessage());
        assertEquals("TOOL_IDEMPOTENCY_UNAVAILABLE", readError.getMessage());
        assertEquals(0, lockExecutions.get());
        assertEquals(0, readExecutions.get());
    }

    @Test
    void usesConfiguredTtlForRunningAndSucceededStates() {
        Fixture fixture = fixture();
        fixture.properties.setToolIdempotencyTtlSeconds(123L);

        fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.FILE_READ,
                Map.of(), () -> Map.of("content", "ready"));

        verify(fixture.bucket, times(2)).set(anyString(), eq(123L), eq(TimeUnit.SECONDS));
    }

    @Test
    void rejectsUnsafeScopeIdsBeforeAccessingRedis() {
        Fixture fixture = fixture();

        BusinessException requestError = assertThrows(BusinessException.class,
                () -> fixture.service.execute(42L, "../req", "call-1", InternalAiTool.FILE_READ,
                        Map.of(), Map::of));
        BusinessException callError = assertThrows(BusinessException.class,
                () -> fixture.service.execute(42L, "req-1", "call/1", InternalAiTool.FILE_READ,
                        Map.of(), Map::of));

        assertEquals(40000, requestError.getCode());
        assertEquals(40000, callError.getCode());
        verify(fixture.client, never()).getLock(anyString());
    }

    @Test
    void sharesCompletedResultAcrossServiceInstances() {
        Fixture fixture = fixture();
        ToolInvocationIdempotencyService second = new ToolInvocationIdempotencyService(
                fixture.client, new ObjectMapper(), fixture.properties);
        AtomicInteger executions = new AtomicInteger();

        fixture.service.execute(42L, "req-1", "call-1", InternalAiTool.DIR_READ,
                Map.of("relativeDirPath", "src"),
                () -> { executions.incrementAndGet(); return Map.of("entries", java.util.List.of("App.vue")); });
        second.execute(42L, "req-1", "call-1", InternalAiTool.DIR_READ,
                Map.of("relativeDirPath", "src"),
                () -> { executions.incrementAndGet(); return Map.of(); });

        assertEquals(1, executions.get());
    }

    private Fixture fixture() {
        RedissonClient client = mock(RedissonClient.class);
        RLock lock = mock(RLock.class);
        @SuppressWarnings("unchecked")
        RBucket<String> bucket = mock(RBucket.class);
        AtomicReference<String> value = new AtomicReference<>();
        AiEngineProperties properties = new AiEngineProperties();
        properties.setToolIdempotencyTtlSeconds(86400L);
        properties.setToolIdempotencyLockWaitMillis(30000L);

        when(client.getLock(anyString())).thenReturn(lock);
        when(client.<String>getBucket(anyString())).thenReturn(bucket);
        try {
            when(lock.tryLock(30000L, TimeUnit.MILLISECONDS)).thenReturn(true);
        } catch (InterruptedException exception) {
            throw new AssertionError(exception);
        }
        when(lock.isHeldByCurrentThread()).thenReturn(true);
        when(bucket.get()).thenAnswer(invocation -> value.get());
        doAnswer(invocation -> {
            value.set(invocation.getArgument(0));
            return null;
        }).when(bucket).set(anyString(), anyLong(), eq(TimeUnit.SECONDS));
        when(bucket.delete()).thenAnswer(invocation -> value.getAndSet(null) != null);

        ToolInvocationIdempotencyService service = new ToolInvocationIdempotencyService(
                client, new ObjectMapper(), properties);
        return new Fixture(client, lock, bucket, value, properties, service);
    }

    private record Fixture(
            RedissonClient client,
            RLock lock,
            RBucket<String> bucket,
            AtomicReference<String> value,
            AiEngineProperties properties,
            ToolInvocationIdempotencyService service) { }
}
