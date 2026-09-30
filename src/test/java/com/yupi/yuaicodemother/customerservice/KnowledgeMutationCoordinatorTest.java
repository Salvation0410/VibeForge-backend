package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeMutationLeaseMapper;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeMutationLease;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.junit.jupiter.api.Test;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class KnowledgeMutationCoordinatorTest {
    @Test
    void signsValidatesAndRejectsTamperingAndRevocation() {
        var mapper = mock(CustomerServiceKnowledgeMutationLeaseMapper.class);
        var rows = new HashMap<String, CustomerServiceKnowledgeMutationLease>();
        when(mapper.lockNextFence()).thenReturn(7L);
        when(mapper.advanceFence(7)).thenReturn(1);
        when(mapper.countDocumentConflicts(any(), any())).thenReturn(0L);
        when(mapper.insertLease(any())).thenAnswer(call -> {
            var row = call.getArgument(0, CustomerServiceKnowledgeMutationLease.class);
            rows.put(row.getOperationId(), row);
            return 1;
        });
        when(mapper.findByOperationId(any())).thenAnswer(call -> rows.get(call.getArgument(0)));
        when(mapper.revoke(any(), anyLong())).thenAnswer(call -> {
            var row = rows.get(call.getArgument(0));
            row.setRevokedAt(LocalDateTime.ofInstant(Instant.parse("2030-01-01T00:00:01Z"), ZoneOffset.UTC));
            return 1;
        });
        var ai = new AiEngineProperties();
        ai.setToken("test-secret");
        var props = new CustomerServiceProperties();
        props.setLeaseTimeoutSeconds(30);
        var coordinator = new KnowledgeMutationCoordinator(mapper, ai, props,
                Clock.fixed(Instant.parse("2030-01-01T00:00:00Z"), ZoneOffset.UTC));

        var lease = coordinator.acquire("document:12", "op_1", "INDEX", "worker-a");
        assertEquals(7, lease.fence());
        verify(mapper).countDocumentConflicts(eq("document:12"), any());
        verify(mapper).advanceFence(7);
        assertTrue(coordinator.validate(lease).verified());
        assertFalse(coordinator.validate(new KnowledgeMutationCoordinator.Lease(
                lease.scope(), lease.operationId(), lease.operation(), lease.fence(), lease.expiresAt(), lease.proof() + "x")).verified());
        coordinator.revoke(lease);
        assertFalse(coordinator.validate(lease).current());
    }

    @Test
    void expiredLeaseIsNotCurrent() {
        var mapper = mock(CustomerServiceKnowledgeMutationLeaseMapper.class);
        var ai = new AiEngineProperties(); ai.setToken("test-secret");
        var props = new CustomerServiceProperties(); props.setLeaseTimeoutSeconds(1);
        var issuing = new KnowledgeMutationCoordinator(mapper, ai, props,
                Clock.fixed(Instant.parse("2030-01-01T00:00:00Z"), ZoneOffset.UTC));
        when(mapper.lockNextFence()).thenReturn(1L);
        when(mapper.advanceFence(1)).thenReturn(1);
        when(mapper.insertLease(any())).thenReturn(1);
        var lease = issuing.acquire("document:1", "op_expire", "INDEX", "worker");
        var row = new CustomerServiceKnowledgeMutationLease();
        row.setOperationId(lease.operationId()); row.setScope(lease.scope()); row.setOperation(lease.operation());
        row.setFence(lease.fence()); row.setExpiresAt(LocalDateTime.ofEpochSecond(lease.expiresAt(), 0, ZoneOffset.UTC));
        when(mapper.findByOperationId(lease.operationId())).thenReturn(row);
        var later = new KnowledgeMutationCoordinator(mapper, ai, props,
                Clock.fixed(Instant.parse("2030-01-01T00:00:02Z"), ZoneOffset.UTC));
        assertFalse(later.validate(lease).current());
    }

    @Test
    void rebuildUsesGlobalConflictCheck() {
        var mapper = mock(CustomerServiceKnowledgeMutationLeaseMapper.class);
        when(mapper.lockNextFence()).thenReturn(9L);
        when(mapper.countCollectionConflicts(any())).thenReturn(1L);
        var ai = new AiEngineProperties();
        ai.setToken("test-secret");
        assertThrows(IllegalStateException.class, () -> new KnowledgeMutationCoordinator(
                mapper, ai, new CustomerServiceProperties(), Clock.systemUTC())
                .acquire("collection", "op_2", "REBUILD", "worker"));
    }
}
