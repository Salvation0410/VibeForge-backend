package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.ai.gateway.GenerationStreamException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.mapper.GenerationFenceMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.LocalDateTime;
import java.util.Map;

/** 持久化单调生成代际，并以 MySQL 事务决定不可变产物的活动版本。 */
@Service
public class GenerationFenceService {
    private final GenerationFenceMapper mapper;
    private final ObjectMapper objectMapper;
    private final TransactionTemplate transactions;

    @Autowired
    public GenerationFenceService(GenerationFenceMapper mapper, ObjectMapper objectMapper,
                                  PlatformTransactionManager transactionManager) {
        this.mapper = mapper;
        this.objectMapper = objectMapper;
        this.transactions = new TransactionTemplate(transactionManager);
    }

    /** 在事务中递增应用代际；事务提交后该 token 永不复用。 */
    public long issue(long appId) {
        try {
            Long value = transactions.execute(status -> {
                mapper.ensureFenceRow(appId);
                if (mapper.incrementFence(appId) != 1) throw unavailable();
                Long current = mapper.currentFence(appId);
                if (current == null || current <= 0) throw unavailable();
                return current;
            });
            if (value == null) throw unavailable();
            return value;
        } catch (GenerationStreamException error) {
            throw error;
        } catch (RuntimeException error) {
            throw unavailable(error);
        }
    }

    /** 将旧文件指针种为 token 0 基线，确保首次 fencing 切换仍保留当前版本。 */
    public void seedLegacyActive(long appId, String codeGenType, String requestId,
                                 long sequence, Map<String, String> hashes) {
        if (requestId == null || requestId.isBlank()) return;
        transactions.executeWithoutResult(status -> {
            mapper.ensureFenceRow(appId);
            mapper.seedActive(appId, codeGenType, requestId, sequence, json(hashes), LocalDateTime.now());
        });
    }

    /** 只有最新代际可登记发布并原子切换活动版本；历史发布事实不可变。 */
    public void activate(long appId, String codeGenType, String requestId, long fenceToken,
                         long sequence, Map<String, String> hashes) {
        transactions.executeWithoutResult(status -> {
            Long current = mapper.lockCurrentFence(appId);
            if (current == null || current != fenceToken) {
                throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(),
                        "GENERATION_FENCE_REJECTED", requestId, "生成租约代际已失效，拒绝激活产物", null);
            }
            PublicationRecord existing = mapper.findPublication(appId, codeGenType, requestId);
            String hashesJson = json(hashes);
            if (existing == null) {
                mapper.insertPublication(appId, codeGenType, requestId, fenceToken, sequence, hashesJson, LocalDateTime.now());
            } else if (existing.fenceToken() != fenceToken || existing.publicationSequence() != sequence
                    || !readHashes(existing.hashesJson()).equals(hashes)) {
                throw new ArtifactValidationException("ARTIFACT_PUBLISH_CONFLICT", null,
                        "相同请求 ID 的发布事实与已有记录不一致");
            }
            mapper.updateActive(appId, codeGenType, requestId, fenceToken, LocalDateTime.now());
        });
    }

    public ActiveArtifact active(long appId, String codeGenType) {
        return mapper.findActive(appId, codeGenType);
    }

    public PublicationRecord publication(long appId, String codeGenType, String requestId) {
        return mapper.findPublication(appId, codeGenType, requestId);
    }

    public boolean fencingEnabled(long appId) {
        return mapper.currentFence(appId) != null;
    }

    private String json(Map<String, String> hashes) {
        try { return objectMapper.writeValueAsString(hashes); }
        catch (JsonProcessingException error) { throw new IllegalStateException("产物摘要序列化失败", error); }
    }

    private Map<String, String> readHashes(String value) {
        try { return objectMapper.readValue(value, new TypeReference<>() { }); }
        catch (JsonProcessingException error) { throw new IllegalStateException("已保存的产物摘要无效", error); }
    }

    private GenerationStreamException unavailable() {
        return unavailable(null);
    }

    private GenerationStreamException unavailable(Throwable cause) {
        return new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), "GENERATION_FENCE_UNAVAILABLE",
                null, "生成代际服务暂不可用，请稍后重试", cause);
    }

    public record ActiveArtifact(String requestId, Long fenceToken) { }
    public record PublicationRecord(Long appId, String codeGenType, String requestId, Long fenceToken,
                                    Long publicationSequence, String hashesJson) { }
}
