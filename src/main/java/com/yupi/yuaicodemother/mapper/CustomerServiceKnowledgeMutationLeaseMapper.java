package com.yupi.yuaicodemother.mapper;

import com.mybatisflex.core.BaseMapper;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeMutationLease;
import org.apache.ibatis.annotations.Insert;
import org.apache.ibatis.annotations.Param;
import org.apache.ibatis.annotations.Select;
import org.apache.ibatis.annotations.Update;

import java.time.LocalDateTime;

public interface CustomerServiceKnowledgeMutationLeaseMapper extends BaseMapper<CustomerServiceKnowledgeMutationLease> {
    @Select("SELECT 1")
    int ping();

    @Select("SELECT nextFence FROM customer_service_knowledge_mutation_guard WHERE id=1 FOR UPDATE")
    Long lockNextFence();

    @Update("UPDATE customer_service_knowledge_mutation_guard SET nextFence=nextFence+1 WHERE id=1 AND nextFence=#{fence}")
    int advanceFence(@Param("fence") long fence);

    @Select("SELECT COUNT(*) FROM customer_service_knowledge_mutation_lease WHERE revokedAt IS NULL AND expiresAt>#{now} AND (scope=#{scope} OR scope='collection')")
    long countDocumentConflicts(@Param("scope") String scope, @Param("now") LocalDateTime now);

    @Select("SELECT COUNT(*) FROM customer_service_knowledge_mutation_lease WHERE revokedAt IS NULL AND expiresAt>#{now}")
    long countCollectionConflicts(@Param("now") LocalDateTime now);

    @Insert("INSERT INTO customer_service_knowledge_mutation_lease(operationId,scope,operation,fence,expiresAt,owner) VALUES(#{operationId},#{scope},#{operation},#{fence},#{expiresAt},#{owner})")
    int insertLease(CustomerServiceKnowledgeMutationLease lease);

    @Select("SELECT operationId,scope,operation,fence,expiresAt,revokedAt,owner,createTime,updateTime FROM customer_service_knowledge_mutation_lease WHERE operationId=#{operationId}")
    CustomerServiceKnowledgeMutationLease findByOperationId(@Param("operationId") String operationId);

    @Update("UPDATE customer_service_knowledge_mutation_lease SET revokedAt=NOW() WHERE operationId=#{operationId} AND fence=#{fence} AND revokedAt IS NULL")
    int revoke(@Param("operationId") String operationId, @Param("fence") long fence);
}
