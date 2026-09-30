package com.yupi.yuaicodemother.service;

import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeMutationLeaseMapper;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeMutationLease;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.HexFormat;
import java.util.Set;

@Service
public class KnowledgeMutationCoordinator {
    private static final Set<String> OPERATIONS = Set.of("INDEX", "DELETE", "REBUILD");
    private static final String KEY_CONTEXT = "customer-service-knowledge-lease:v1\0";

    private final CustomerServiceKnowledgeMutationLeaseMapper mapper;
    private final AiEngineProperties aiProperties;
    private final CustomerServiceProperties properties;
    private final Clock clock;

    public KnowledgeMutationCoordinator(CustomerServiceKnowledgeMutationLeaseMapper mapper,
                                        AiEngineProperties aiProperties,
                                        CustomerServiceProperties properties) {
        this(mapper, aiProperties, properties, Clock.systemUTC());
    }

    public KnowledgeMutationCoordinator(CustomerServiceKnowledgeMutationLeaseMapper mapper,
                                        AiEngineProperties aiProperties,
                                        CustomerServiceProperties properties,
                                        Clock clock) {
        this.mapper = mapper;
        this.aiProperties = aiProperties;
        this.properties = properties;
        this.clock = clock;
    }

    @Transactional
    public Lease acquire(String scope, String operationId, String operation, String owner) {
        validateFields(scope, operationId, operation, owner);
        requireSigningSecret();
        LocalDateTime now = LocalDateTime.ofInstant(clock.instant(), ZoneOffset.UTC);
        Long fence = mapper.lockNextFence();
        if (fence == null || fence < 1) throw new IllegalStateException("KNOWLEDGE_MUTATION_COORDINATOR_NOT_READY");
        long conflicts = "collection".equals(scope)
                ? mapper.countCollectionConflicts(now)
                : mapper.countDocumentConflicts(scope, now);
        if (conflicts > 0) throw new IllegalStateException("KNOWLEDGE_MUTATION_SCOPE_BUSY");
        if (mapper.advanceFence(fence) != 1) throw new IllegalStateException("KNOWLEDGE_MUTATION_FENCE_CONFLICT");
        long expiresAt = clock.instant().plusSeconds(Math.max(1, properties.getLeaseTimeoutSeconds())).getEpochSecond();
        CustomerServiceKnowledgeMutationLease row = new CustomerServiceKnowledgeMutationLease();
        row.setOperationId(operationId);
        row.setScope(scope);
        row.setOperation(operation);
        row.setFence(fence);
        row.setExpiresAt(LocalDateTime.ofEpochSecond(expiresAt, 0, ZoneOffset.UTC));
        row.setOwner(owner);
        if (mapper.insertLease(row) != 1) throw new IllegalStateException("KNOWLEDGE_MUTATION_LEASE_CREATE_FAILED");
        return signed(scope, operationId, operation, fence, expiresAt);
    }

    public Validation validate(Lease lease) {
        if (lease == null || !validProof(lease)) return Validation.invalid(lease);
        CustomerServiceKnowledgeMutationLease row = mapper.findByOperationId(lease.operationId());
        boolean current = row != null && row.getRevokedAt() == null
                && row.getExpiresAt() != null
                && row.getExpiresAt().toEpochSecond(ZoneOffset.UTC) == lease.expiresAt()
                && lease.expiresAt() > clock.instant().getEpochSecond()
                && row.getFence() == lease.fence()
                && lease.scope().equals(row.getScope())
                && lease.operation().equals(row.getOperation());
        return new Validation(true, current, lease.scope(), lease.operationId(), lease.operation(),
                lease.fence(), lease.expiresAt());
    }

    public void revoke(Lease lease) {
        if (lease != null) mapper.revoke(lease.operationId(), lease.fence());
    }

    public boolean ready() {
        try {
            requireSigningSecret();
            return mapper.ping() == 1;
        } catch (RuntimeException error) {
            return false;
        }
    }

    private Lease signed(String scope, String operationId, String operation, long fence, long expiresAt) {
        return new Lease(scope, operationId, operation, fence, expiresAt,
                HexFormat.of().formatHex(hmac(payload(scope, operationId, operation, fence, expiresAt))));
    }

    private boolean validProof(Lease lease) {
        try {
            byte[] supplied = HexFormat.of().parseHex(lease.proof());
            byte[] expected = hmac(payload(lease.scope(), lease.operationId(), lease.operation(), lease.fence(), lease.expiresAt()));
            return MessageDigest.isEqual(expected, supplied);
        } catch (RuntimeException error) {
            return false;
        }
    }

    private byte[] hmac(String value) {
        try {
            byte[] key = MessageDigest.getInstance("SHA-256")
                    .digest((KEY_CONTEXT + requireSigningSecret()).getBytes(StandardCharsets.UTF_8));
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(key, "HmacSHA256"));
            return mac.doFinal(value.getBytes(StandardCharsets.UTF_8));
        } catch (Exception error) {
            throw new IllegalStateException("KNOWLEDGE_MUTATION_PROOF_FAILED", error);
        }
    }

    private String requireSigningSecret() {
        String token = aiProperties.getToken();
        if (token == null || token.isBlank()) throw new IllegalStateException("KNOWLEDGE_MUTATION_SIGNING_SECRET_MISSING");
        return token;
    }

    private static String payload(String scope, String operationId, String operation, long fence, long expiresAt) {
        return scope + "\n" + operationId + "\n" + operation + "\n" + fence + "\n" + expiresAt;
    }

    private static void validateFields(String scope, String operationId, String operation, String owner) {
        if (scope == null || (!"collection".equals(scope) && !scope.matches("document:[A-Za-z0-9_-]{1,128}")))
            throw new IllegalArgumentException("invalid mutation scope");
        if (operationId == null || !operationId.matches("[A-Za-z0-9_-]{1,128}"))
            throw new IllegalArgumentException("invalid operation id");
        if (!OPERATIONS.contains(operation) || owner == null || owner.isBlank())
            throw new IllegalArgumentException("invalid mutation operation");
        if (("collection".equals(scope)) != "REBUILD".equals(operation))
            throw new IllegalArgumentException("mutation scope and operation mismatch");
    }

    public record Lease(String scope, String operationId, String operation, long fence, long expiresAt,
                        String proof) { }

    public record Validation(boolean verified, boolean current, String scope, String operationId,
                             String operation, long fence, long expiresAt) {
        static Validation invalid(Lease lease) {
            return new Validation(false, false, lease == null ? null : lease.scope(),
                    lease == null ? null : lease.operationId(), lease == null ? null : lease.operation(),
                    lease == null ? 0 : lease.fence(), lease == null ? 0 : lease.expiresAt());
        }
    }
}
