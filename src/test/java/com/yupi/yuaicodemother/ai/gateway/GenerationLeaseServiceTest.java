package com.yupi.yuaicodemother.ai.gateway;

import org.junit.jupiter.api.Test;
import org.redisson.api.RBucket;
import org.redisson.api.RLock;
import org.redisson.api.RedissonClient;
import com.yupi.yuaicodemother.core.artifact.GenerationFenceService;

import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.atomic.AtomicBoolean;

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
        when(transitionLock.tryLock()).thenReturn(true);
        when(client.<String>getBucket("ai:generation:state:42")).thenReturn(state);
        when(lock.tryLock()).thenReturn(true);
        var service = service(client);

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
        RLock transitionLock = mock(RLock.class);
        when(client.getLock("ai:generation:transition:42")).thenReturn(transitionLock);
        when(transitionLock.tryLock()).thenReturn(true);
        when(client.getLock("ai:generation:app:42")).thenReturn(lock);
        when(lock.tryLock()).thenReturn(false);
        GenerationStreamException error = assertThrows(GenerationStreamException.class,
                () -> service(client).acquire(42, "req-2"));
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
        when(transitionLock.tryLock()).thenReturn(true);
        when(client.<String>getBucket("ai:generation:state:42")).thenReturn(state);
        AtomicBoolean held = new AtomicBoolean();
        when(appLock.isLocked()).thenAnswer(call -> held.get());
        when(appLock.tryLock()).thenAnswer(call -> { held.set(true); return true; });
        var service = service(client);

        GenerationLease cancelled = service.acquire(42, "req-cancel");
        assertTrue(service.cancel(cancelled));
        assertThrows(GenerationStreamException.class,
                () -> service.commit(42, "req-cancel", () -> "published"));

        value.set("req-commit:ACTIVE");
        assertEquals("published", service.commit(42, "req-commit", () -> "published"));
        assertFalse(service.cancel(new GenerationLease(42, "req-commit", 1L, appLock, 1L)));
        assertEquals("req-commit:COMMITTED", value.get());
    }

    @Test
    void committedArtifactRemainsSuccessfulWhenFinalStateWriteFails() throws Exception {
        RedissonClient client = mock(RedissonClient.class);
        RLock transitionLock = mock(RLock.class);
        RLock appLock = mock(RLock.class);
        when(client.getLock("ai:generation:app:42")).thenReturn(appLock);
        when(appLock.isLocked()).thenReturn(true);
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

        String result = service(client).commit(42, "req-1", () -> "published");

        assertEquals("published", result);
        assertEquals("req-1:COMMITTING", value.get());
    }

    @Test
    void explicitCancellationAndStatusExposeStablePublicStates() {
        RedissonClient client = mock(RedissonClient.class);
        RLock appLock = mock(RLock.class);
        when(client.getLock("ai:generation:app:42")).thenReturn(appLock);
        when(appLock.isLocked()).thenReturn(true);
        RLock transitionLock = mock(RLock.class);
        RBucket<String> state = mock(RBucket.class);
        AtomicReference<String> value = bucketValue(state);
        when(client.getLock("ai:generation:transition:42")).thenReturn(transitionLock);
        when(client.<String>getBucket("ai:generation:state:42")).thenReturn(state);
        var service = service(client);

        assertEquals(GenerationLeaseService.PublicState.IDLE, service.getStatus(42).state());

        value.set("req-1:ACTIVE");
        var firstCancel = service.cancelActive(42);
        assertEquals(GenerationLeaseService.PublicState.STOPPING, firstCancel.state());
        assertTrue(firstCancel.cancellationRequested());
        assertEquals("req-1:CANCELLED", value.get());

        var repeatedCancel = service.cancelActive(42);
        assertEquals(GenerationLeaseService.PublicState.STOPPING, repeatedCancel.state());
        assertFalse(repeatedCancel.cancellationRequested());

        value.set("req-2:COMMITTING");
        var committing = service.cancelActive(42);
        assertEquals(GenerationLeaseService.PublicState.COMMITTING, committing.state());
        assertFalse(committing.cancellationRequested());
        assertEquals("req-2:COMMITTING", value.get());
    }

    @Test
    void lostLeaseRejectsCommitBeforeCallingAction() {
        RedissonClient client = mock(RedissonClient.class);
        RLock appLock = mock(RLock.class);
        RLock transition = mock(RLock.class);
        RBucket<String> state = mock(RBucket.class);
        when(client.getLock("ai:generation:app:42")).thenReturn(appLock);
        when(client.getLock("ai:generation:transition:42")).thenReturn(transition);
        when(client.<String>getBucket("ai:generation:state:42")).thenReturn(state);
        when(state.get()).thenReturn("req:ACTIVE");
        AtomicBoolean invoked = new AtomicBoolean();
        var service = service(client);
        var error = assertThrows(GenerationStreamException.class,
                () -> service.commit(42, "req", () -> { invoked.set(true); return null; }));
        assertEquals("GENERATION_LEASE_LOST", error.getErrorCode());
        assertFalse(invoked.get());
        verify(state, never()).set(anyString());
    }

    @Test
    void staleStateWithoutLockDoesNotAppearRunningOrAcceptCancellation() {
        RedissonClient client = mock(RedissonClient.class);
        RLock appLock = mock(RLock.class);
        RLock transition = mock(RLock.class);
        RBucket<String> state = mock(RBucket.class);
        when(client.getLock("ai:generation:app:42")).thenReturn(appLock);
        when(client.getLock("ai:generation:transition:42")).thenReturn(transition);
        when(client.<String>getBucket("ai:generation:state:42")).thenReturn(state);
        when(state.get()).thenReturn("req:ACTIVE");
        var service = service(client);
        assertEquals(GenerationLeaseService.PublicState.IDLE, service.getStatus(42).state());
        assertEquals(GenerationLeaseService.PublicState.IDLE, service.cancelActive(42).state());
        assertFalse(service.cancel(new GenerationLease(42, "req", 1L, appLock, 1)));
        verify(state, never()).set(anyString());
        verify(state, never()).delete();
    }

    @Test
    void existingLockRejectsSameClientReentryWithoutOverwritingRequest() {
        RedissonClient client = mock(RedissonClient.class);
        RLock appLock = mock(RLock.class);
        RLock transition = mock(RLock.class);
        when(client.getLock("ai:generation:app:42")).thenReturn(appLock);
        when(client.getLock("ai:generation:transition:42")).thenReturn(transition);
        when(transition.tryLock()).thenReturn(true);
        when(appLock.isLocked()).thenReturn(true);
        var error = assertThrows(GenerationStreamException.class,
                () -> service(client).acquire(42, "new-request"));
        assertEquals("GENERATION_IN_PROGRESS", error.getErrorCode());
        verify(appLock, never()).tryLock();
        verify(client, never()).getBucket(anyString());
        verify(transition).unlock();
    }

    @Test
    void busyTransitionRejectsImmediatelyBeforeAcquiringAppLock() {
        RedissonClient client = mock(RedissonClient.class);
        RLock appLock = mock(RLock.class);
        RLock transition = mock(RLock.class);
        when(client.getLock("ai:generation:app:42")).thenReturn(appLock);
        when(client.getLock("ai:generation:transition:42")).thenReturn(transition);
        assertEquals("GENERATION_IN_PROGRESS", assertThrows(GenerationStreamException.class,
                () -> service(client).acquire(42, "req")).getErrorCode());
        verifyNoInteractions(appLock);
        verify(transition, never()).unlock();
    }

    /** 用原子引用模拟 Redis bucket 的读取、写入和删除语义。 */
    private AtomicReference<String> bucketValue(RBucket<String> bucket) {
        AtomicReference<String> value = new AtomicReference<>();
        when(bucket.get()).thenAnswer(invocation -> value.get());
        doAnswer(invocation -> { value.set(invocation.getArgument(0)); return null; }).when(bucket).set(anyString());
        doAnswer(invocation -> { value.set(null); return true; }).when(bucket).delete();
        return value;
    }

    private GenerationLeaseService service(RedissonClient client) {
        var fence = mock(GenerationFenceService.class);
        when(fence.issue(anyLong())).thenReturn(1L);
        return new GenerationLeaseService(client, fence);
    }
}
