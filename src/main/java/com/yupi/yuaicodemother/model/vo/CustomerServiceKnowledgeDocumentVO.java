package com.yupi.yuaicodemother.model.vo;

import lombok.Data;
import java.time.LocalDateTime;

@Data
public class CustomerServiceKnowledgeDocumentVO {
    private Long id;
    private String name;
    private String fileType;
    private Long fileSize;
    private String contentHash;
    private Long documentVersion;
    private Long indexedVersion;
    private Long etlVersion;
    private Integer chunkCount;
    private String status;
    private String lastErrorCode;
    private Long createdBy;
    private Long updatedBy;
    private LocalDateTime createTime;
    private LocalDateTime updateTime;
}
