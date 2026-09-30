package com.yupi.yuaicodemother.mapper;

import com.mybatisflex.core.BaseMapper;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeEtlOutbox;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.time.LocalDateTime;
import java.util.List;

public interface CustomerServiceKnowledgeEtlOutboxMapper extends BaseMapper<CustomerServiceKnowledgeEtlOutbox> {
    @Select("SELECT * FROM customer_service_knowledge_etl_outbox WHERE (status='PENDING' AND nextRetryTime<=#{now}) OR (status='PROCESSING' AND processingDeadline<#{now}) ORDER BY id LIMIT #{limit}")
    List<CustomerServiceKnowledgeEtlOutbox> findClaimCandidates(@Param("now") LocalDateTime now, @Param("limit") int limit);

    @Update("UPDATE customer_service_knowledge_etl_outbox SET status='PROCESSING',processingOwner=#{owner},processingDeadline=#{deadline} WHERE id=#{id} AND ((status='PENDING' AND nextRetryTime<=#{now}) OR (status='PROCESSING' AND processingDeadline<#{now}))")
    int claim(@Param("id") long id, @Param("owner") String owner, @Param("now") LocalDateTime now,
              @Param("deadline") LocalDateTime deadline);

    @Update("UPDATE customer_service_knowledge_etl_outbox SET status=#{status},lastErrorCode=#{code},processingOwner=NULL,processingDeadline=NULL WHERE id=#{id} AND status='PROCESSING' AND processingOwner=#{owner}")
    int finish(@Param("id") long id, @Param("owner") String owner, @Param("status") String status,
               @Param("code") String code);

    @Update("UPDATE customer_service_knowledge_etl_outbox SET status=#{status},retryCount=#{retryCount},nextRetryTime=#{nextRetry},lastErrorCode=#{code},processingOwner=NULL,processingDeadline=NULL WHERE id=#{id} AND status='PROCESSING' AND processingOwner=#{owner}")
    int retry(@Param("id") long id, @Param("owner") String owner, @Param("status") String status,
              @Param("retryCount") int retryCount, @Param("nextRetry") LocalDateTime nextRetry,
              @Param("code") String code);

    @Select("SELECT * FROM customer_service_knowledge_etl_outbox WHERE documentId=#{documentId} ORDER BY createTime DESC,id DESC LIMIT #{limit}")
    List<CustomerServiceKnowledgeEtlOutbox> history(@Param("documentId") long documentId, @Param("limit") int limit);
}
