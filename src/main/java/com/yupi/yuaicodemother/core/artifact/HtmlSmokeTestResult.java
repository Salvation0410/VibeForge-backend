package com.yupi.yuaicodemother.core.artifact;

/** 浏览器烟测结果；失败码区分页面故障与验证环境不可用。 */
public record HtmlSmokeTestResult(boolean passed, String errorCode, String message) {
    public static HtmlSmokeTestResult success() {
        return new HtmlSmokeTestResult(true, null, null);
    }

    public static HtmlSmokeTestResult failure(String errorCode, String message) {
        return new HtmlSmokeTestResult(false, errorCode, message);
    }
}
