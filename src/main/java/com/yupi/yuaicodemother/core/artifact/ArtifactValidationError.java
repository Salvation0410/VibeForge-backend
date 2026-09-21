package com.yupi.yuaicodemother.core.artifact;

/** 表示一个可定位到具体文件的确定性校验错误。 */
public record ArtifactValidationError(String code, String file, String message) { }
