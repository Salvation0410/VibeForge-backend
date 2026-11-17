package com.yupi.yuaicodemother.model.entity;
import lombok.Data;
import java.time.LocalDateTime;

/** 不可重授的首次准备资格；期限仅表示观测失联，不是执行结束证明。 */
@Data
public class KnowledgePrepareExecution {
    private long taskId;
    private String operationId;
    private long fence;
    private String executorId;
    private String status;
    private LocalDateTime observationDeadline;
    private String receiptJson;
}
