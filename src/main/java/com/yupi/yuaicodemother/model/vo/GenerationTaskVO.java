package com.yupi.yuaicodemother.model.vo;

import lombok.Data;
import java.time.LocalDateTime;

/** 执行记录、实时租约和发布事实分别展示，不推断跨资源原子终态。 */
@Data
public class GenerationTaskVO {
    private String requestId;
    private Long appId;
    private String codeGenType;
    private String executionState;
    private String errorCode;
    private LocalDateTime cancelRequestedAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
    private LocalDateTime finishedAt;
    private LocalDateTime queryUntil;
    private String leaseState;
    private String publicationState;
}
