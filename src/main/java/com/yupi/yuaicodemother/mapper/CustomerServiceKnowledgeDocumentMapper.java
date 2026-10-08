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

    // OSS 只保留当前对象，旧 indexedVersion 在对象替换后无法重新构建，
    // 因此 collection 重建只接受当前版本已完整索引且处于 ACTIVE 状态的文档。
    @Select("SELECT * FROM customer_service_knowledge_document WHERE isDelete=0 AND status='ACTIVE' AND indexedVersion>0 AND indexedVersion=documentVersion ORDER BY id LIMIT #{limit}")
    List<CustomerServiceKnowledgeDocument> listRebuildableLimited(@Param("limit") int limit);

    @Update("UPDATE customer_service_knowledge_document SET status='INDEXING',lastErrorCode=NULL WHERE id=#{id} AND documentVersion=#{version} AND etlVersion=#{etlVersion} AND status IN ('UPLOADED','FAILED','ACTIVE','INDEXING') AND isDelete=0")
    int markIndexing(@Param("id") long id, @Param("version") long version, @Param("etlVersion") long etlVersion);

    @Update("UPDATE customer_service_knowledge_document SET indexedVersion=#{version},chunkCount=#{chunkCount},status='ACTIVE',lastErrorCode=NULL WHERE id=#{id} AND documentVersion=#{version} AND etlVersion=#{etlVersion} AND status='INDEXING' AND isDelete=0")
    int completeIndex(@Param("id") long id, @Param("version") long version,
                      @Param("etlVersion") long etlVersion, @Param("chunkCount") int chunkCount);

    @Update("UPDATE customer_service_knowledge_document SET status='FAILED',lastErrorCode=#{code} WHERE id=#{id} AND documentVersion=#{version} AND etlVersion=#{etlVersion} AND isDelete=0 AND status IN ('UPLOADED','FAILED','INDEXING')")
    int failIndex(@Param("id") long id, @Param("version") long version,
                  @Param("etlVersion") long etlVersion, @Param("code") String code);
}
