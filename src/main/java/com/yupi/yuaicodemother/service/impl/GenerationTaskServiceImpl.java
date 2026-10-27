package com.yupi.yuaicodemother.service.impl;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.ai.gateway.GenerationStreamException;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.mapper.GenerationTaskMapper;
import com.yupi.yuaicodemother.model.entity.GenerationTask;
import com.yupi.yuaicodemother.service.GenerationTaskService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.dao.DuplicateKeyException;

import java.time.LocalDateTime;
import java.util.Set;

/** 插入失败关闭生成；终态写失败不得反转已经发布的业务结果。 */
@Service
@RequiredArgsConstructor
@Slf4j
public class GenerationTaskServiceImpl implements GenerationTaskService {
    private final GenerationTaskMapper mapper;
    private static final Set<String> TERMINAL = Set.of("SUCCEEDED", "FAILED", "CANCELLED");

    @Override
    public void start(long appId, long userId, String requestId, CodeGenTypeEnum type) {
        GenerationTask task = new GenerationTask();
        task.setRequestId(requestId);
        task.setAppId(appId);
        task.setUserId(userId);
        task.setCodeGenType(type.getValue());
        task.setCreatedAt(LocalDateTime.now());
        task.setUpdatedAt(task.getCreatedAt());
        try {
            if (mapper.createTask(task) == 1) return;
        } catch (DuplicateKeyException error) {
            throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), "GENERATION_REQUEST_EXISTS",
                    requestId, "该生成请求已存在，拒绝重复执行", error);
        } catch (RuntimeException error) {
            // JDBC 异常可能带 SQL 和连接信息，禁止直接通过 SSE 返回底层异常消息。
            throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), "GENERATION_TASK_UNAVAILABLE",
                    requestId, "生成任务记录暂不可用，请稍后重试", error);
        }
        throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), "GENERATION_TASK_UNAVAILABLE",
                requestId, "生成任务记录暂不可用，请稍后重试", null);
    }

    @Override
    public void finish(String requestId, String state, String errorCode) {
        if (!TERMINAL.contains(state)) throw new IllegalArgumentException("非法任务终态");
        // 仅保存稳定代码，避免把供应商异常或源码误写入任务表。
        String safeCode = errorCode != null && errorCode.matches("[A-Za-z0-9_.:-]{1,128}")
                ? errorCode : (errorCode == null ? null : "GENERATION_FAILED");
        LocalDateTime now = LocalDateTime.now();
        try {
            mapper.finishTask(requestId, state, safeCode, now, now.plusDays(30));
        } catch (RuntimeException error) {
            log.warn("生成任务终态记录失败, requestId={}, type={}", requestId, error.getClass().getSimpleName());
        }
    }

    @Override
    public void recordCancellation(String requestId) {
        try {
            // 取消线程落账可能晚于引擎终态；仅补首次观测，不改终态或延长查询保留期。
            mapper.recordCancellation(requestId, LocalDateTime.now());
        } catch (RuntimeException error) {
            log.warn("生成取消墓碑记录失败, requestId={}, type={}", requestId, error.getClass().getSimpleName());
        }
    }

    @Override
    public GenerationTask findOwned(long appId, long userId, String requestId) {
        if (requestId != null && (!requestId.matches("[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}"))) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "请求 ID 非法");
        }
        GenerationTask task = mapper.findOwned(appId, userId, requestId, LocalDateTime.now());
        if (task == null) throw new BusinessException(ErrorCode.NOT_FOUND_ERROR, "任务不存在或已超过查询保留期");
        return task;
    }
}
