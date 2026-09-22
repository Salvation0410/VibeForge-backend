package com.yupi.yuaicodemother.core.builder;

public record VueBuildResult(boolean built, String errorCode, String message) {

    public static VueBuildResult success() {
        return new VueBuildResult(true, "", "");
    }

    public static VueBuildResult failure(String code, String message) {
        return new VueBuildResult(false, code, message);
    }
}
