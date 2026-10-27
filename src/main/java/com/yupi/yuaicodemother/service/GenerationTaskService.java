package com.yupi.yuaicodemother.service;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.model.entity.GenerationTask;

/** 任务账本不是租约，不提供恢复授权或状态认领接口。 */
public interface GenerationTaskService {
    void start(long appId, long userId, String requestId, CodeGenTypeEnum type, long fenceToken);
    long findFenceToken(long appId, String requestId);
    void finish(String requestId, String state, String errorCode);
    void recordCancellation(String requestId);
    GenerationTask findOwned(long appId, long userId, String requestId);
}
