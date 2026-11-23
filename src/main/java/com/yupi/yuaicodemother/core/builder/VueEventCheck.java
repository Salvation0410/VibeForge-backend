package com.yupi.yuaicodemother.core.builder;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Set;

/** 默认透传的疑似错接仅交给审查；只有关闭透传且声明完整时才作阻断。 */
final class VueEventCheck {
    private static final Logger log = LoggerFactory.getLogger(VueEventCheck.class);
    private static final ObjectMapper JSON = new ObjectMapper()
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION);

    static VueBuildResult parse(String output) {
        try {
            if (output.length() > 8000) return unavailable();
            JsonNode result = JSON.readTree(output);
            if (result == null || !result.isObject() || result.size() != 8
                    || !result.path("version").isIntegralNumber() || !result.path("version").canConvertToInt()
                    || result.path("version").intValue() != 1 || !result.path("truncated").isBoolean()) return unavailable();
            int checked = count(result, "checkedUsages"), skipped = count(result, "skippedUsages");
            String reason = result.path("reason").asText("");
            if (!Set.of("NONE", "INCOMPLETE").contains(reason)
                    || result.path("truncated").booleanValue() != reason.equals("INCOMPLETE")) return unavailable();
            List<String> issues = evidence(result.path("issues")), advisories = evidence(result.path("advisories"));
            String status = result.path("status").asText("");
            if (reason.equals("INCOMPLETE") && (checked != 0 || !issues.isEmpty() || !advisories.isEmpty())) return unavailable();
            if ((!issues.isEmpty() || !advisories.isEmpty()) && checked == 0) return unavailable();
            if (!(status.equals("ok") && issues.isEmpty()) && !(status.equals("failed") && !issues.isEmpty())) return unavailable();
            log.info("Vue 事件检查 status={} checkedUsages={} skippedUsages={} reason={} issues={} advisories={}",
                    status, checked, skipped, reason, issues.size(), advisories.size());
            if (!issues.isEmpty()) return VueBuildResult.failure("VUE_EVENT_CONTRACT_FAILED",
                    "关闭属性透传的组件未声明所监听的自定义事件：" + String.join("；", issues));
            String summary = reason.equals("INCOMPLETE") ? "事件检查已跳过不完整源码"
                    : "有限事件检查未发现确定性阻断（checked=" + checked + ", skipped=" + skipped + "）";
            if (!advisories.isEmpty()) summary += "；仅供审查的事件疑似错接，默认属性透传不能据此判错：" + String.join("；", advisories);
            return new VueBuildResult(true, "", summary + "；不代表完整事件或浏览器验收通过");
        } catch (Exception exception) {
            return unavailable();
        }
    }

    private static List<String> evidence(JsonNode values) {
        if (!values.isArray() || values.size() > 4) throw new IllegalArgumentException();
        List<String> details = new ArrayList<>();
        for (JsonNode issue : values) {
            if (!issue.isObject() || issue.size() != 3) throw new IllegalArgumentException();
            String file = text(issue, "file"), component = text(issue, "component"), event = text(issue, "event");
            if (file.isBlank() || file.length() > 180 || file.startsWith("/") || file.contains("\\") || file.contains(":")
                    || file.chars().anyMatch(Character::isISOControl) || Arrays.asList(file.split("/")).contains("..")
                    || !file.endsWith(".vue") || !component.matches("[$A-Za-z_][\\w$-]{0,79}")
                    || !event.matches("[$A-Za-z_][\\w$-]{0,79}")) throw new IllegalArgumentException();
            details.add(file + " -> " + component + " @" + event);
        }
        return details;
    }

    private static String text(JsonNode node, String key) {
        if (!node.path(key).isTextual()) throw new IllegalArgumentException();
        return node.path(key).textValue();
    }

    private static int count(JsonNode node, String key) {
        var value = node.path(key);
        if (!value.isIntegralNumber() || !value.canConvertToInt() || value.intValue() < 0 || value.intValue() > 20000) throw new IllegalArgumentException();
        return value.intValue();
    }

    static VueBuildResult unavailable() {
        return VueBuildResult.failure("VUE_EVENT_CHECK_UNAVAILABLE", "Vue 事件契约检查不可用，请检查 Node、Vue compiler 或检查器执行环境");
    }
}
