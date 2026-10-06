package com.yupi.yuaicodemother.model.entity;

import com.mybatisflex.annotation.Column;
import com.mybatisflex.annotation.Id;
import com.mybatisflex.annotation.KeyType;
import com.mybatisflex.annotation.Table;
import com.mybatisflex.core.keygen.KeyGenerators;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serial;
import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@Table("customer_service_knowledge_etl_outbox")
public class CustomerServiceKnowledgeEtlOutbox implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    @Id(keyType = KeyType.Generator, value = KeyGenerators.snowFlakeId)
    private Long id;

    @Column("documentId")
    private Long documentId;

    @Column("documentVersion")
    private Long documentVersion;

    @Column("etlVersion")
    private Long etlVersion;

    private String operation;

    private String status;

    @Column("retryCount")
    private Integer retryCount;

    @Column("nextRetryTime")
    private LocalDateTime nextRetryTime;

    @Column("lastErrorCode")
    private String lastErrorCode;

    @Column("processingOwner")
    private String processingOwner;

    @Column("processingDeadline")
    private LocalDateTime processingDeadline;

    @Column("createTime")
    private LocalDateTime createTime;

    @Column("updateTime")
    private LocalDateTime updateTime;
}
