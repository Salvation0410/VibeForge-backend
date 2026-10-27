package com.yupi.yuaicodemother.mapper;

import com.mybatisflex.core.BaseMapper;
import com.yupi.yuaicodemother.model.entity.GenerationTask;
import org.apache.ibatis.annotations.Param;

import java.time.LocalDateTime;

/** 条件更新防止迟到终态覆盖，唯一键防止同一请求再次执行。 */
public interface GenerationTaskMapper extends BaseMapper<GenerationTask> {
    int createTask(GenerationTask task);
    int finishTask(@Param("requestId") String requestId, @Param("state") String state,
                   @Param("errorCode") String errorCode, @Param("now") LocalDateTime now,
                   @Param("queryUntil") LocalDateTime queryUntil);
    int recordCancellation(@Param("requestId") String requestId, @Param("now") LocalDateTime now);
    Long findFenceToken(@Param("appId") long appId, @Param("requestId") String requestId);
    GenerationTask findOwned(@Param("appId") long appId, @Param("userId") long userId,
                             @Param("requestId") String requestId, @Param("now") LocalDateTime now);
}
