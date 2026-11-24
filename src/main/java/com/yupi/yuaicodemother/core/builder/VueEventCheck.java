package com.yupi.yuaicodemother.core.builder;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
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
            if (result == null || !result.isObject() || result.size() != 14
                    || !result.path("version").isIntegralNumber() || !result.path("version").canConvertToInt()
                    || result.path("version").intValue() != 3 || !result.path("truncated").isBoolean()) return unavailable();
            int checked = count(result, "checkedUsages"), skipped = count(result, "skippedUsages");
            int payloadChecked = count(result, "payloadChecked"), payloadSkipped = count(result, "payloadSkipped");
            int argumentChecked = count(result, "argumentChecked"), argumentSkipped = count(result, "argumentSkipped");
            String reason = result.path("reason").asText("");
            if (!Set.of("NONE", "INCOMPLETE").contains(reason)
                    || result.path("truncated").booleanValue() != reason.equals("INCOMPLETE")) return unavailable();
            List<String> issues = evidence(result.path("issues")), advisories = evidence(result.path("advisories"));
            List<String> payloads = payloadEvidence(result.path("payloadAdvisories"));
            List<String> arguments = argumentEvidence(result.path("argumentAdvisories"));
            String status = result.path("status").asText("");
            if (reason.equals("INCOMPLETE") && (checked != 0 || payloadChecked != 0 || payloadSkipped != 0
                    || argumentChecked != 0 || argumentSkipped != 0
                    || !issues.isEmpty() || !advisories.isEmpty() || !payloads.isEmpty() || !arguments.isEmpty())) return unavailable();
            // 载荷可覆盖不遮蔽直接监听器的有限 v-for，与事件名硬门禁独立计数。
            if ((!issues.isEmpty() || !advisories.isEmpty()) && checked == 0) return unavailable();
            if (payloads.size() > payloadChecked) return unavailable();
            // 字面参数线索与名称、对象载荷检查独立覆盖，不据此升级事件硬门禁。
            if (arguments.size() > argumentChecked) return unavailable();
            if (!(status.equals("ok") && issues.isEmpty()) && !(status.equals("failed") && !issues.isEmpty())) return unavailable();
            log.info("Vue 事件检查 status={} checkedUsages={} skippedUsages={} reason={} issues={} advisories={} payloadChecked={} payloadSkipped={} payloadAdvisories={} argumentChecked={} argumentSkipped={} argumentAdvisories={}",
                    status, checked, skipped, reason, issues.size(), advisories.size(), payloadChecked, payloadSkipped, payloads.size(),
                    argumentChecked, argumentSkipped, arguments.size());
            if (!issues.isEmpty()) return VueBuildResult.failure("VUE_EVENT_CONTRACT_FAILED",
                    "关闭属性透传的组件未声明所监听的自定义事件：" + String.join("；", issues));
            String summary = reason.equals("INCOMPLETE") ? "事件检查已跳过不完整源码"
                    : "有限事件检查未发现确定性阻断（checked=" + checked + ", skipped=" + skipped + "）";
            if (!advisories.isEmpty()) summary += "；仅供审查的事件疑似错接，默认属性透传不能据此判错：" + String.join("；", advisories);
            summary += "；有限载荷检查（checked=" + payloadChecked + ", skipped=" + payloadSkipped + "）";
            if (!payloads.isEmpty()) summary += "；仅供审查的事件载荷线索：" + String.join("；", payloads)
                    + "。须结合源码确认转发、默认值及功能影响，不能仅凭线索判错";
            summary += "；有限字面参数检查（checked=" + argumentChecked + ", skipped=" + argumentSkipped + "）";
            if (!arguments.isEmpty()) summary += "；仅供审查的事件字面参数线索：" + String.join("；", arguments)
                    + "。多个不同字面载荷的零形参监听可能丢失目标或状态，须结合源码确认实际消费与功能影响；合法忽略参数不能仅凭线索判错";
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
            details.add(location(issue));
        }
        return details;
    }

    /** 仅投影固定类别与白名单位置，不接收字面值、表达式或任意源码。 */
    private static List<String> argumentEvidence(JsonNode values) {
        if (!values.isArray() || values.size() > 4) throw new IllegalArgumentException();
        List<String> details = new ArrayList<>();
        for (JsonNode issue : values) {
            if (!issue.isObject() || issue.size() != 4 || !text(issue, "kind").equals("IGNORED_LITERAL_VARIANTS")) throw new IllegalArgumentException();
            details.add("IGNORED_LITERAL_VARIANTS " + location(issue));
        }
        return details;
    }

    /** 白名单只投影固定类别及有限缺键，原始表达式和任意脚本文本不进入构建摘要。 */
    private static List<String> payloadEvidence(JsonNode values) {
        if (!values.isArray() || values.size() > 4) throw new IllegalArgumentException();
        List<String> details = new ArrayList<>();
        for (JsonNode issue : values) {
            if (!issue.isObject() || issue.size() != 5 || !text(issue, "kind").equals("MISSING_DESTRUCTURED_KEYS")) throw new IllegalArgumentException();
            String at = location(issue);
            JsonNode keys = issue.path("missingKeys");
            if (!keys.isArray() || keys.isEmpty() || keys.size() > 4) throw new IllegalArgumentException();
            Set<String> unique = new HashSet<>();
            List<String> names = new ArrayList<>();
            for (JsonNode key : keys) {
                if (!key.isTextual() || !key.textValue().matches("[$A-Za-z_][\\w$-]{0,79}") || !unique.add(key.textValue())) throw new IllegalArgumentException();
                names.add(key.textValue());
            }
            details.add("MISSING_DESTRUCTURED_KEYS " + at + " 缺键=" + String.join(",", names));
        }
        return details;
    }

    private static String location(JsonNode issue) {
        String file = text(issue, "file"), component = text(issue, "component"), event = text(issue, "event");
        if (file.isBlank() || file.length() > 180 || file.startsWith("/") || file.contains("\\") || file.contains(":")
                || file.chars().anyMatch(Character::isISOControl) || Arrays.asList(file.split("/")).contains("..")
                || !file.endsWith(".vue") || !component.matches("[$A-Za-z_][\\w$-]{0,79}")
                || !event.matches("[$A-Za-z_][\\w$-]{0,79}")) throw new IllegalArgumentException();
        return file + " -> " + component + " @" + event;
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
