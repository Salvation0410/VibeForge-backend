package com.yupi.yuaicodemother.ai.gateway;

import org.junit.jupiter.api.Test;
import org.redisson.api.RBucket;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;

import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class GenerationLeaseServiceTest {
    @Test
    void acquiresAndReleasesWithOriginalOwnerThread() {
        RedissonClient client = mock(RedissonClient.class);
        RLock lock = mock(RLock.class);
        RLock transitionLock = mock(RLock.class);
        RBucket<String> state = mock(RBucket.class);
        AtomicReference<String> value = bucketValue(state);
        when(client.getLock("ai:generation:app:42")).thenReturn(lock);
        when(client.getLock("ai:generation:transition:42")).thenReturn(transitionLock);
        when(client.<String>getBucket("ai:generation:state:42")).thenReturn(state);
        when(lock.tryLock()).thenReturn(true);
        var service = new GenerationLeaseService(client);

        GenerationLease lease = service.acquire(42, "req-1");
        service.release(lease);

        assertEquals(Thread.currentThread().threadId(), lease.ownerThreadId());
        assertNull(value.get());
        verify(lock).unlockAsync(lease.ownerThreadId());
    }

    @Test
    void rejectsSecondGenerationWithoutWaiting() {
        RedissonClient client = mock(RedissonClient.class);
        RLock lock = mock(RLock.class);
        when(client.getLock("ai:generation:app:42")).thenReturn(lock);
        when(lock.tryLock()).thenReturn(false);
        GenerationStreamException error = assertThrows(GenerationStreamException.class,
                () -> new GenerationLeaseService(client).acquire(42, "req-2"));
        assertEquals("GENERATION_IN_PROGRESS", error.getErrorCode());
        assertEquals("req-2", error.getRequestId());
    }

    @Test
    void cancelAndCommitHaveSingleAtomicWinner() throws Exception {
        RedissonClient client = mock(RedissonClient.class);
        RLock appLock = mock(RLock.class);
        RLock transitionLock = mock(RLock.class);
        RBucket<String> state = mock(RBucket.class);
        AtomicReference<String> value = bucketValue(state);
        when(client.getLock("ai:generation:app:42")).thenReturn(appLock);
        when(client.getLock("ai:generation:transition:42")).thenReturn(transitionLock);
        when(client.<String>getBucket("ai:generation:state:42")).thenReturn(state);
        when(appLock.tryLock()).thenReturn(true);
        var service = new GenerationLeaseService(client);

        GenerationLease cancelled = service.acquire(42, "req-cancel");
        assertTrue(service.cancel(cancelled));
        assertThrows(GenerationStreamException.class,
                () -> service.commit(42, "req-cancel", () -> "published"));

        value.set("req-commit:ACTIVE");
        assertEquals("published", service.commit(42, "req-commit", () -> "published"));
        assertFalse(service.cancel(new GenerationLease(42, "req-commit", appLock, 1L)));
        assertEquals("req-commit:COMMITTED", value.get());
    }

    @Test
    void committedArtifactRemainsSuccessfulWhenFinalStateWriteFails() throws Exception {
        RedissonClient client = mock(RedissonClient.class);
        RLock transitionLock = mock(RLock.class);
        RBucket<String> state = mock(RBucket.class);
        AtomicReference<String> value = new AtomicReference<>("req-1:ACTIVE");
        when(client.getLock("ai:generation:transition:42")).thenReturn(transitionLock);
        when(client.<String>getBucket("ai:generation:state:42")).thenReturn(state);
        when(state.get()).thenAnswer(invocation -> value.get());
        doAnswer(invocation -> {
            String next = invocation.getArgument(0);
            if (next.endsWith(":COMMITTED")) throw new IllegalStateException("redis unavailable");
            value.set(next);
            return null;
        }).when(state).set(anyString());

        String result = new GenerationLeaseService(client).commit(42, "req-1", () -> "published");

        assertEquals("published", result);
        assertEquals("req-1:COMMITTING", value.get());
    }

    /** 用原子引用模拟 Redis bucket 的读取、写入和删除语义。 */
    private AtomicReference<String> bucketValue(RBucket<String> bucket) {
        AtomicReference<String> value = new AtomicReference<>();
        when(bucket.get()).thenAnswer(invocation -> value.get());
        doAnswer(invocation -> { value.set(invocation.getArgument(0)); return null; }).when(bucket).set(anyString());
        doAnswer(invocation -> { value.set(null); return true; }).when(bucket).delete();
        return value;
    }
}
