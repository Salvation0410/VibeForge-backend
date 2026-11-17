package com.yupi.yuaicodemother.model.vo;

import java.time.Instant;
import com.fasterxml.jackson.annotation.JsonFormat;

/** 仅解释准备记录；COMPLETE不是发布证明，UNKNOWN不授权接管。时间统一UTC。 */
public record KnowledgePrepareObservationVO(long taskId, String recordedState, String observationState,
        String reasonCode, @JsonFormat(shape = JsonFormat.Shape.STRING) Instant observedAt,
        @JsonFormat(shape = JsonFormat.Shape.STRING) Instant observationDeadline) { }
