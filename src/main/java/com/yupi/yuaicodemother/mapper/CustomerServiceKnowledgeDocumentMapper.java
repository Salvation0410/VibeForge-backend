package com.yupi.yuaicodemother.mapper;

import com.mybatisflex.core.BaseMapper;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import org.apache.ibatis.annotations.Param;
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

    @Select("SELECT * FROM customer_service_knowledge_document WHERE isDelete=0 ORDER BY id")
    List<CustomerServiceKnowledgeDocument> listAllActive();

    @Update("UPDATE customer_service_knowledge_document SET status='INDEXING',lastErrorCode=NULL WHERE id=#{id} AND documentVersion=#{version} AND etlVersion=#{etlVersion} AND status IN ('UPLOADED','FAILED','ACTIVE')")
    int markIndexing(@Param("id") long id, @Param("version") long version, @Param("etlVersion") long etlVersion);

    @Update("UPDATE customer_service_knowledge_document SET indexedVersion=#{version},chunkCount=#{chunkCount},status='ACTIVE',lastErrorCode=NULL WHERE id=#{id} AND documentVersion=#{version} AND etlVersion=#{etlVersion} AND status='INDEXING' AND isDelete=0")
    int completeIndex(@Param("id") long id, @Param("version") long version,
                      @Param("etlVersion") long etlVersion, @Param("chunkCount") int chunkCount);

    @Update("UPDATE customer_service_knowledge_document SET status='FAILED',lastErrorCode=#{code} WHERE id=#{id} AND documentVersion=#{version} AND etlVersion=#{etlVersion} AND status='INDEXING'")
    int failIndex(@Param("id") long id, @Param("version") long version,
                  @Param("etlVersion") long etlVersion, @Param("code") String code);
}
