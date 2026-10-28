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
import org.springframework.scheduling.annotation.Scheduled;
import jakarta.annotation.PreDestroy;

import java.time.LocalDateTime;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicBoolean;

/** 插入失败关闭生成；终态写失败不得反转已经发布的业务结果。 */
@Service
@RequiredArgsConstructor
@Slf4j
public class GenerationTaskServiceImpl implements GenerationTaskService {
    private final GenerationTaskMapper mapper;
    private static final Set<String> TERMINAL = Set.of("SUCCEEDED", "FAILED", "CANCELLED");
    private final String executionOwner = UUID.randomUUID().toString();
    private final ConcurrentHashMap<String, OwnedExecution> executions = new ConcurrentHashMap<>();

    /** 进程内登记不是恢复队列；只包含本实例收到 INSERT 成功确认的执行。 */
    private record OwnedExecution(long appId, long fenceToken, AtomicBoolean renewalEnabled) { }

    @Override
    public void start(long appId, long userId, String requestId, CodeGenTypeEnum type, long fenceToken) {
        GenerationTask task = new GenerationTask();
        task.setRequestId(requestId);
        task.setAppId(appId);
        task.setUserId(userId);
        task.setCodeGenType(type.getValue());
        task.setFenceToken(fenceToken);
        task.setExecutionOwner(executionOwner);
        task.setCreatedAt(LocalDateTime.now());
        task.setUpdatedAt(task.getCreatedAt());
        try {
            if (mapper.createTask(task) == 1) {
                executions.put(requestId, new OwnedExecution(appId, fenceToken, new AtomicBoolean(true)));
                return;
            }
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
    public long findFenceToken(long appId, String requestId) {
        Long token;
        try {
            token = mapper.findFenceToken(appId, requestId);
        } catch (RuntimeException error) {
            throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), "GENERATION_FENCE_UNAVAILABLE",
                    requestId, "生成任务代际暂不可用，请稍后重试", error);
        }
        if (token == null || token <= 0) {
            throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(), "GENERATION_FENCE_UNAVAILABLE",
                    requestId, "生成任务代际暂不可用，请稍后重试", null);
        }
        return token;
    }

    @Override
    public void finish(String requestId, String state, String errorCode) {
        if (!TERMINAL.contains(state)) throw new IllegalArgumentException("非法任务终态");
        OwnedExecution execution = executions.get(requestId);
        // 重建实例没有旧执行资格；旧回调也不能用请求 ID 单独结束后继所有者的任务。
        if (execution == null) return;
        // 仅保存稳定代码，避免把供应商异常或源码误写入任务表。
        String safeCode = errorCode != null && errorCode.matches("[A-Za-z0-9_.:-]{1,128}")
                ? errorCode : (errorCode == null ? null : "GENERATION_FAILED");
        LocalDateTime now = LocalDateTime.now();
        try {
            mapper.finishTask(requestId, state, safeCode, now, now.plusDays(30),
                    executionOwner, execution.fenceToken());
        } catch (RuntimeException error) {
            log.warn("生成任务终态记录失败, requestId={}, type={}", requestId, error.getClass().getSimpleName());
        } finally {
            // 终态写入失败也不能继续制造“存活”假象；发布事实由独立查询核对。
            executions.remove(requestId, execution);
        }
    }

    /** 心跳仅表示 Spring 控制器存活；过期不会自动改终态、抢占或恢复 Python。 */
    @Scheduled(fixedDelay = 15_000, initialDelay = 15_000, scheduler = "generationTaskHeartbeatScheduler")
    public void heartbeatOwnedExecutions() {
        executions.forEach((requestId, execution) -> {
            if (!execution.renewalEnabled().get()) return;
            try {
                if (mapper.heartbeat(requestId, execution.appId(), executionOwner, execution.fenceToken()) != 1) {
                    execution.renewalEnabled().set(false);
                    log.warn("生成任务心跳资格失效，停止续写, requestId={}", requestId);
                }
            } catch (RuntimeException error) {
                // 暂时失败可在数据库期限内重试；SQL 条件保证超过期限后不会复活。
                log.warn("生成任务心跳写入失败, requestId={}, type={}", requestId, error.getClass().getSimpleName());
            }
        });
    }

    @PreDestroy
    public void clearOwnedExecutions() {
        executions.clear();
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
