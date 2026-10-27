package com.yupi.yuaicodemother.model.entity;

import com.mybatisflex.annotation.Id;
import com.mybatisflex.annotation.Table;
import lombok.Data;

import java.time.LocalDateTime;

/** Spring 所有的任务账本；不保存提示词、源码或底层异常正文。 */
@Data
@Table("generation_task")
public class GenerationTask {
    @Id
    private String requestId;
    private Long appId;
    private Long userId;
    private String codeGenType;
    private String executionState;
    private Long fenceToken = 0L;
    private String errorCode;
    private LocalDateTime cancelRequestedAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
    private LocalDateTime finishedAt;
    private LocalDateTime queryUntil;
}
