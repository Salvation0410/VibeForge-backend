package com.yupi.yuaicodemother.core.artifact;

import java.util.List;

/** 汇总候选产物的确定性校验结果。 */
public record ArtifactValidationResult(boolean valid, List<ArtifactValidationError> errors) {
    /** 固化错误列表，避免调用方在校验完成后修改结果。 */
    public ArtifactValidationResult {
        errors = List.copyOf(errors);
    }
}
