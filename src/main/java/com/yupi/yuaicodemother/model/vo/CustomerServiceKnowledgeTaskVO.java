package com.yupi.yuaicodemother.model.vo;

import lombok.Data;
import java.time.LocalDateTime;

@Data
public class CustomerServiceKnowledgeTaskVO {
    private Long id;
    private Long documentId;
    private Long documentVersion;
    private Long etlVersion;
    private String operation;
    private String status;
    private Integer retryCount;
    private String lastErrorCode;
    private LocalDateTime nextRetryTime;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
