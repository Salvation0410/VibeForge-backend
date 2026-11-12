package com.yupi.yuaicodemother.core.builder;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Set;

/** 仅接收可信检查器的有界协议，不将脚本原始输出放入公共失败信息。 */
final class VueAnchorCheck {
    private static final ObjectMapper JSON = new ObjectMapper()
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION);
    private static final Set<String> REASONS = Set.of("NONE", "INCOMPLETE", "DYNAMIC_PAGE", "NO_APP_TEMPLATE");

    static VueBuildResult parse(String output) {
        try {
            if (output.length() > 8000) return unavailable();
            JsonNode result = JSON.readTree(output);
            if (result == null || !result.isObject() || result.size() != 7
                    || !result.path("version").isIntegralNumber() || !result.path("version").canConvertToInt()
                    || result.path("version").intValue() != 1 || !result.path("truncated").isBoolean()) return unavailable();
            int checked = count(result, "checkedLinks");
            count(result, "skippedLinks");
            String reason = result.path("reason").asText("");
            if (!REASONS.contains(reason)) return unavailable();
            var issues = result.path("issues");
            if (!issues.isArray() || issues.size() > 4 || issues.size() > checked) return unavailable();
            String status = result.path("status").asText("");
            if (status.equals("ok") && issues.isEmpty()) {
                if (!reason.equals("NONE") && checked != 0) return unavailable();
                if (result.path("truncated").booleanValue() && !reason.equals("INCOMPLETE")) return unavailable();
                return new VueBuildResult(true, "", reason.equals("NONE")
                        ? "静态锚点检查未发现缺失目标；不代表目标唯一、可见或浏览器交互通过"
                        : "静态锚点检查已跳过不支持或不完整的页面，不能据此证明导航可用");
            }
            if (!status.equals("failed") || issues.isEmpty() || !reason.equals("NONE")
                    || result.path("truncated").booleanValue()) return unavailable();
            List<String> details = new ArrayList<>();
            for (JsonNode issue : issues) {
                if (!issue.isObject() || issue.size() != 2 || !issue.path("file").isTextual()
                        || !issue.path("target").isTextual()) return unavailable();
                String file = issue.path("file").textValue();
                String target = issue.path("target").textValue();
                if (file.isBlank() || file.length() > 180 || file.startsWith("/") || file.contains("\\") || file.contains(":")
                        || file.chars().anyMatch(Character::isISOControl) || Arrays.asList(file.split("/")).contains("..")
                        || !(file.endsWith(".vue") || file.equals("index.html"))
                        || !target.matches("[A-Za-z][A-Za-z0-9_-]{0,79}")) return unavailable();
                details.add(file + " -> #" + target);
            }
            return VueBuildResult.failure("VUE_ANCHOR_TARGET_MISSING", "静态同页链接缺少目标：" + String.join("；", details));
        } catch (Exception exception) {
            return unavailable();
        }
    }

    private static int count(JsonNode result, String field) {
        var value = result.path(field);
        if (!value.isIntegralNumber() || !value.canConvertToInt() || value.intValue() < 0 || value.intValue() > 20000) {
            throw new IllegalArgumentException();
        }
        return value.intValue();
    }

    static VueBuildResult unavailable() {
        return VueBuildResult.failure("VUE_ANCHOR_CHECK_UNAVAILABLE", "Vue 静态锚点检查不可用，请检查 Node、Vue compiler 或检查器执行环境");
    }
}
