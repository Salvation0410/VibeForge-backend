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
@Table("customer_service_knowledge_document")
public class CustomerServiceKnowledgeDocument implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    @Id(keyType = KeyType.Generator, value = KeyGenerators.snowFlakeId)
    private Long id;

    private String name;

    @Column("fileType")
    private String fileType;

    @Column("objectKey")
    private String objectKey;

    @Column("fileSize")
    private Long fileSize;

    @Column("contentHash")
    private String contentHash;

    @Column("documentVersion")
    private Long documentVersion;

    @Column("indexedVersion")
    private Long indexedVersion;

    @Column("etlVersion")
    private Long etlVersion;

    // 只有明确重新启用才产生新激活；禁用墓碑撤销当前及更早激活。
    @Column("activationRevision")
    private Long activationRevision;

    // serving 身份独立于正在处理的内容/索引修订，失败时保留旧可用版本。
    @Column("indexedEtlVersion")
    private Long indexedEtlVersion;

    @Column("indexedActivationRevision")
    private Long indexedActivationRevision;

    @Column("chunkCount")
    private Integer chunkCount;

    private String status;

    @Column("lastErrorCode")
    private String lastErrorCode;

    @Column("createdBy")
    private Long createdBy;

    @Column("updatedBy")
    private Long updatedBy;

    @Column("createTime")
    private LocalDateTime createTime;

    @Column("updateTime")
    private LocalDateTime updateTime;

    @Column(value = "isDelete", isLogicDelete = true)
    private Integer isDelete;
}
