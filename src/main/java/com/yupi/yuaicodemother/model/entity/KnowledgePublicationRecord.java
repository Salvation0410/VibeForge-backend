package com.yupi.yuaicodemother.model.entity;
import lombok.Data;

/** 只保存不可变收据，不保存签名租约或下载链接。 */
@Data
public class KnowledgePublicationRecord {
    private String publicationId;
    private long epoch;
    private String receiptJson;
}
