package com.yupi.yuaicodemother.core.builder;

import java.util.List;
import java.util.Map;

public record VueBuildResult(boolean built, String errorCode, String message,
                             List<Map<String, String>> componentIssues) {

    public VueBuildResult {
        // 结构化证据仅来自可信检查器；固定三字段结果继续走原兼容入口。
        componentIssues = componentIssues.stream().map(Map::copyOf).toList();
    }

    public VueBuildResult(boolean built, String errorCode, String message) {
        this(built, errorCode, message, List.of());
    }

    public static VueBuildResult success() {
        return new VueBuildResult(true, "", "");
    }

    public static VueBuildResult failure(String code, String message) {
        return new VueBuildResult(false, code, message);
    }
}
