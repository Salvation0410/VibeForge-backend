package com.yupi.yuaicodemother.core.builder;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

/** 可信脚本通过现有命令执行边界运行，检查器原始输出不进入日志或公共错误。 */
final class VueComponentContractCheck {
    private static final Logger log = LoggerFactory.getLogger(VueComponentContractCheck.class);
    private static final ObjectMapper JSON = new ObjectMapper()
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
            .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION);

    static VueBuildResult verify(File directory, VueProjectBuilder.CommandRunner runner) throws InterruptedException {
        Path script = null;
        try (var resource = VueComponentContractCheck.class.getResourceAsStream("/vue/component-contract-check.cjs")) {
            if (resource == null) return unavailable();
            script = Files.createTempFile("vue-component-contract-", ".cjs");
            Files.copy(resource, script, java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            // Node 直接接收参数，不经 cmd 拼接；中断及进程树清理由原 CommandRunner 负责。
            var output = runner.run(directory, List.of("node", script.toString(), directory.getAbsolutePath()), 15);
            if (!output.succeeded() || output.stdout().length() > 8000 || !output.stderr().isBlank()) return unavailable();
            return parse(output.stdout());
        } catch (InterruptedException exception) {
            throw exception;
        } catch (Exception exception) {
            return unavailable();
        } finally {
            if (script != null) {
                try { Files.deleteIfExists(script); }
                catch (Exception ignored) { log.warn("Vue 组件检查临时脚本清理失败"); }
            }
        }
    }

    static VueBuildResult parse(String output) {
        try {
            JsonNode result = JSON.readTree(output);
            if (result == null || !result.isObject() || !result.path("version").isIntegralNumber() || !result.path("version").canConvertToInt()
                    || result.path("version").intValue() != 1 || !result.path("status").isTextual()) return unavailable();
            if (result.size() != 8 || !result.path("truncated").isBoolean()) return unavailable();
            for (String key : List.of("checkedComponents", "checkedUsages", "skippedFiles", "skippedUsages")) count(result, key);
            JsonNode issues = result.path("issues");
            if (!issues.isArray() || issues.size() > 4) return unavailable();
            String status = result.path("status").textValue();
            if (status.equals("ok") && issues.isEmpty()) {
                // 只记录统计，不把路径、源码或异常内容当作诊断日志。
                log.info("Vue 组件检查 completed checkedComponents={} checkedUsages={} skippedFiles={} skippedUsages={} truncated={}",
                        count(result, "checkedComponents"), count(result, "checkedUsages"),
                        count(result, "skippedFiles"), count(result, "skippedUsages"), result.path("truncated").asBoolean(true));
                return new VueBuildResult(true, "", "Vue 必填属性检查未发现阻断问题；仅覆盖静态可解析组件，并非完整功能验收");
            }
            if (!status.equals("failed") || issues.isEmpty()) return unavailable();
            List<String> details = new ArrayList<>();
            for (JsonNode issue : issues) {
                if (!issue.isObject() || issue.size() != 3) return unavailable();
                String file = field(issue, "file", 180);
                String component = field(issue, "component", 80);
                String prop = field(issue, "prop", 80);
                if (file.startsWith("/") || file.contains("\\") || file.contains(":")
                        || Arrays.asList(file.split("/")).contains("..") || !file.endsWith(".vue")) return unavailable();
                details.add(file + " -> " + component + "." + prop);
            }
            return VueBuildResult.failure("VUE_COMPONENT_CONTRACT_FAILED", "本地组件缺少显式必填属性：" + String.join("；", details));
        } catch (Exception exception) {
            return unavailable();
        }
    }

    private static String field(JsonNode node, String key, int limit) {
        JsonNode value = node.path(key);
        if (!value.isTextual() || value.textValue().isBlank() || value.textValue().length() > limit
                || value.textValue().chars().anyMatch(Character::isISOControl)) throw new IllegalArgumentException();
        return value.textValue();
    }

    private static int count(JsonNode node, String key) {
        JsonNode value = node.path(key);
        if (!value.isIntegralNumber() || !value.canConvertToInt() || value.intValue() < 0 || value.intValue() > 20000) throw new IllegalArgumentException();
        return value.intValue();
    }

    private static VueBuildResult unavailable() {
        return VueBuildResult.failure("VUE_COMPONENT_CHECK_UNAVAILABLE", "Vue 组件契约检查不可用，请检查 Node、Vue compiler 或检查器执行环境");
    }
}
