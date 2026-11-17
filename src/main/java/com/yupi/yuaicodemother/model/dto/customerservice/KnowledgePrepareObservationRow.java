package com.yupi.yuaicodemother.model.dto.customerservice;

import lombok.Data;
import java.time.LocalDateTime;

/** 单条任务的有界只读投影，不加载执行者身份和收据。 */
@Data
public class KnowledgePrepareObservationRow {
    private long taskId;
    private String status;
    private LocalDateTime observationDeadline;
}
