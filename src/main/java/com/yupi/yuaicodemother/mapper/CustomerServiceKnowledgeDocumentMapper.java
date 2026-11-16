package com.yupi.yuaicodemother.mapper;

import com.mybatisflex.core.BaseMapper;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.util.List;

public interface CustomerServiceKnowledgeDocumentMapper extends BaseMapper<CustomerServiceKnowledgeDocument> {
    @Select("SELECT * FROM customer_service_knowledge_document WHERE id=#{id}")
    CustomerServiceKnowledgeDocument findIncludingDeleted(@Param("id") long id);

    @Select("SELECT * FROM customer_service_knowledge_document WHERE id=#{id} FOR UPDATE")
    CustomerServiceKnowledgeDocument findIncludingDeletedForUpdate(@Param("id") long id);

    @Select("SELECT * FROM customer_service_knowledge_document WHERE contentHash=#{hash} AND isDelete=0 AND (#{excludeId} IS NULL OR id<>#{excludeId}) LIMIT 1")
    CustomerServiceKnowledgeDocument findDuplicate(@Param("hash") String hash, @Param("excludeId") Long excludeId);

    @Select("SELECT * FROM customer_service_knowledge_document WHERE isDelete=0 ORDER BY createTime DESC,id DESC LIMIT #{limit} OFFSET #{offset}")
    List<CustomerServiceKnowledgeDocument> pageDocuments(@Param("offset") long offset, @Param("limit") int limit);

    @Select("SELECT COUNT(*) FROM customer_service_knowledge_document WHERE isDelete=0")
    long countDocuments();

    // 左连接保留缺失历史原文的候选，由 worker 明确失败，不能静默缩小重建集合。
    @Select("SELECT d.id,d.documentVersion,d.etlVersion,d.activationRevision,d.indexedVersion,d.indexedEtlVersion,d.indexedActivationRevision,d.status,d.isDelete,c.name,c.fileType,c.objectKey,c.contentHash FROM customer_service_knowledge_document d LEFT JOIN customer_service_knowledge_content_revision c ON c.documentId=d.id AND c.documentVersion=d.indexedVersion WHERE d.isDelete=0 AND d.status IN ('ACTIVE','UPLOADED','INDEXING','FAILED') AND d.indexedVersion>0 AND d.indexedActivationRevision=d.activationRevision ORDER BY d.id LIMIT #{limit}")
    List<CustomerServiceKnowledgeDocument> listRebuildableLimited(@Param("limit") int limit);

    // 每个内容版本登记独立 OSS 原文，后续替换不得覆盖旧 serving 使用的对象键。
    @Insert("INSERT INTO customer_service_knowledge_content_revision(documentId,documentVersion,name,fileType,objectKey,fileSize,contentHash,createTime) VALUES(#{id},#{documentVersion},#{name},#{fileType},#{objectKey},#{fileSize},#{contentHash},CURRENT_TIMESTAMP)")
    int insertContentRevision(CustomerServiceKnowledgeDocument document);

    @Update("UPDATE customer_service_knowledge_document SET status='INDEXING',lastErrorCode=NULL WHERE id=#{id} AND documentVersion=#{version} AND etlVersion=#{etlVersion} AND status IN ('UPLOADED','FAILED','ACTIVE','INDEXING') AND isDelete=0")
    int markIndexing(@Param("id") long id, @Param("version") long version, @Param("etlVersion") long etlVersion);

    @Update("UPDATE customer_service_knowledge_document SET indexedVersion=#{version},indexedEtlVersion=#{etlVersion},indexedActivationRevision=activationRevision,chunkCount=#{chunkCount},status='ACTIVE',lastErrorCode=NULL WHERE id=#{id} AND documentVersion=#{version} AND etlVersion=#{etlVersion} AND status='INDEXING' AND isDelete=0")
    int completeIndex(@Param("id") long id, @Param("version") long version,
                      @Param("etlVersion") long etlVersion, @Param("chunkCount") int chunkCount);

    @Update("UPDATE customer_service_knowledge_document SET status='FAILED',lastErrorCode=#{code} WHERE id=#{id} AND documentVersion=#{version} AND etlVersion=#{etlVersion} AND isDelete=0 AND status IN ('UPLOADED','FAILED','INDEXING')")
    int failIndex(@Param("id") long id, @Param("version") long version,
                  @Param("etlVersion") long etlVersion, @Param("code") String code);
}
