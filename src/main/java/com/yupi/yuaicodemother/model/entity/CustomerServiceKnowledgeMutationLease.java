package com.yupi.yuaicodemother.model.entity;

import com.mybatisflex.annotation.Column;
import com.mybatisflex.annotation.Id;
import com.mybatisflex.annotation.Table;
import lombok.Data;

import java.time.LocalDateTime;

@Data
@Table("customer_service_knowledge_mutation_lease")
public class CustomerServiceKnowledgeMutationLease {
    @Id
    @Column("operationId")
    private String operationId;
    private String scope;
    private String operation;
    private Long fence;
    @Column("expiresAt")
    private LocalDateTime expiresAt;
    @Column("revokedAt")
    private LocalDateTime revokedAt;
    private String owner;
    @Column("createTime")
    private LocalDateTime createTime;
    @Column("updateTime")
    private LocalDateTime updateTime;
}
