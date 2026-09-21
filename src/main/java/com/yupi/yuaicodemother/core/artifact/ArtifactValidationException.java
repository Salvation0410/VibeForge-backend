package com.yupi.yuaicodemother.core.artifact;

import lombok.Getter;

@Getter
public class ArtifactValidationException extends RuntimeException {
    private final String errorCode;
    private final String file;

    /** 创建一个带稳定错误码和可选文件定位的产物校验异常。 */
    public ArtifactValidationException(String errorCode, String file, String message) {
        super(message);
        this.errorCode = errorCode;
        this.file = file;
    }
}
