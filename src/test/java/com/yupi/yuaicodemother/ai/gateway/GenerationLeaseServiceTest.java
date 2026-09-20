package com.yupi.yuaicodemother.ai.gateway;

import org.junit.jupiter.api.Test;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class GenerationLeaseServiceTest {
    @Test
    void acquiresAndReleasesWithOriginalOwnerThread() {
        RedissonClient client = mock(RedissonClient.class);
        RLock lock = mock(RLock.class);
        when(client.getLock("ai:generation:app:42")).thenReturn(lock);
        when(lock.tryLock()).thenReturn(true);
        var service = new GenerationLeaseService(client);

        GenerationLease lease = service.acquire(42, "req-1");
        service.release(lease);

        assertEquals(Thread.currentThread().threadId(), lease.ownerThreadId());
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
}
