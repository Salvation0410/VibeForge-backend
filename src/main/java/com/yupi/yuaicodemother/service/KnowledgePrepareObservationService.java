package com.yupi.yuaicodemother.service;

import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.mapper.KnowledgePublicationMapper;
import com.yupi.yuaicodemother.model.vo.KnowledgePrepareObservationVO;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import java.time.Clock;
import java.time.ZoneOffset;

/** 单次无锁查询，不改变资格、不探测候选或恢复状态。 */
@Service
public class KnowledgePrepareObservationService {
    private final KnowledgePublicationMapper mapper;
    private final Clock clock;

    @Autowired
    public KnowledgePrepareObservationService(KnowledgePublicationMapper mapper) {
        this(mapper, Clock.systemUTC());
    }
    public KnowledgePrepareObservationService(KnowledgePublicationMapper mapper, Clock clock) {
        this.mapper = mapper; this.clock = clock;
    }

    public KnowledgePrepareObservationVO observe(long documentId, long taskId) {
        if (documentId < 1 || taskId < 1) throw new BusinessException(ErrorCode.PARAMS_ERROR);
        // 一条联合查询核对归属；数据库异常继续传播，不能伪装成未登记。
        var row = mapper.observePrepareExecution(documentId, taskId);
        if (row == null) throw new BusinessException(ErrorCode.NOT_FOUND_ERROR);
        var now = clock.instant();
        var deadline = row.getObservationDeadline() == null ? null : row.getObservationDeadline().toInstant(ZoneOffset.UTC);
        String recorded = row.getStatus();
        String state; String reason;
        if (recorded == null) {
            recorded = "NOT_REGISTERED"; state = "NOT_REGISTERED"; reason = "NO_EXECUTION_RECORD";
        } else if ("STARTED".equals(recorded) && deadline != null) {
            state = deadline.isAfter(now) ? "BUSY" : "UNKNOWN";
            reason = state.equals("BUSY") ? "EXECUTION_IN_PROGRESS" : "OBSERVATION_EXPIRED";
        } else if ("UNKNOWN".equals(recorded)) {
            state = "UNKNOWN"; reason = "EXECUTION_UNCONFIRMED";
        } else if ("COMPLETE".equals(recorded)) {
            state = "COMPLETE"; reason = "COMPLETION_RECORDED";
        } else {
            // 损坏状态不原样输出，避免将任意数据库内容带入管理响应。
            recorded = "INVALID"; state = "UNKNOWN"; reason = "INVALID_EXECUTION_STATE";
        }
        return new KnowledgePrepareObservationVO(taskId, recorded, state, reason, now, deadline);
    }
}
