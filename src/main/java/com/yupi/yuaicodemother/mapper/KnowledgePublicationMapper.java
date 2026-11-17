package com.yupi.yuaicodemother.mapper;

import com.yupi.yuaicodemother.model.entity.KnowledgePublicationState;
import com.yupi.yuaicodemother.model.entity.KnowledgePublicationRecord;
import org.apache.ibatis.annotations.Param;

public interface KnowledgePublicationMapper {
    KnowledgePublicationState state();
    KnowledgePublicationState lockState();
    int advanceEpoch();
    KnowledgePublicationRecord fact(@Param("id") String id);
    int insertFact(@Param("id") String id, @Param("epoch") long epoch, @Param("collection") String collection, @Param("json") String json);
    int activate(@Param("id") String id, @Param("epoch") long epoch);
    int enqueueAlias(@Param("id") String id, @Param("epoch") long epoch);
    int supersedeAliases(@Param("id") String id);
    int acknowledgeAlias(@Param("id") String id, @Param("epoch") long epoch);
    long unfinishedTasks();
    String prepareAnchor(@Param("taskId") long taskId);
    int insertPrepareAnchor(@Param("taskId") long taskId, @Param("json") String json);
    java.util.List<com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.InventoryEntry> inventory(
            @Param("afterEpoch") long afterEpoch, @Param("limit") int limit);
}
