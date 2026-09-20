package com.yupi.yuaicodemother.core.artifact;

import java.util.List;

/** 汇总候选产物的确定性校验结果。 */
public record ArtifactValidationResult(boolean valid, List<ArtifactValidationError> errors) {
    public ArtifactValidationResult {
        errors = List.copyOf(errors);
    }
}
