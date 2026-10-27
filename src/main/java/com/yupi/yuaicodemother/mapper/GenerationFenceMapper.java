package com.yupi.yuaicodemother.mapper;

import com.yupi.yuaicodemother.core.artifact.GenerationFenceService;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;

/** 提供应用级代际和静态产物活动指针的原子 SQL。 */
public interface GenerationFenceMapper {
    int ensureFenceRow(@Param("appId") long appId);
    int incrementFence(@Param("appId") long appId);
    Long currentFence(@Param("appId") long appId);
    Long lockCurrentFence(@Param("appId") long appId);
    int insertPublication(@Param("appId") long appId, @Param("codeGenType") String codeGenType,
                          @Param("requestId") String requestId, @Param("fenceToken") long fenceToken,
                          @Param("sequence") long sequence, @Param("hashesJson") String hashesJson,
                          @Param("publishedAt") LocalDateTime publishedAt);
    GenerationFenceService.PublicationRecord findPublication(@Param("appId") long appId,
            @Param("codeGenType") String codeGenType, @Param("requestId") String requestId);
    GenerationFenceService.ActiveArtifact findActive(@Param("appId") long appId,
            @Param("codeGenType") String codeGenType);
    int seedActive(@Param("appId") long appId, @Param("codeGenType") String codeGenType,
                   @Param("requestId") String requestId, @Param("sequence") long sequence,
                   @Param("hashesJson") String hashesJson, @Param("publishedAt") LocalDateTime publishedAt);
    int updateActive(@Param("appId") long appId, @Param("codeGenType") String codeGenType,
                     @Param("requestId") String requestId, @Param("fenceToken") long fenceToken,
                     @Param("updatedAt") LocalDateTime updatedAt);
}
