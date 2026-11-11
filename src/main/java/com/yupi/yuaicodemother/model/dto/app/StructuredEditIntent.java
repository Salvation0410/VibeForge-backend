package com.yupi.yuaicodemother.model.dto.app;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;

import java.io.IOException;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** 仅表达单个可视化属性修改；定位语法通过不代表真实 DOM 中定位唯一。 */
public record StructuredEditIntent(String version, String selector, String property, String value) {
    private static final ObjectMapper STRICT_MAPPER = new ObjectMapper()
            .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);
    private static final Set<String> FIELDS = Set.of("version", "selector", "property", "value");
    private static final Pattern SELECTOR = Pattern.compile(
            "body(?: > [a-z][a-z0-9-]{0,30}:nth-of-type\\([1-9][0-9]{0,3}\\)){1,12}");
    private static final Set<String> FORBIDDEN_TAGS = Set.of("body", "html", "script", "style", "link", "meta");
    private static final Map<String, String> LABELS = Map.of(
            "color", "文字颜色", "backgroundColor", "背景颜色", "fontSize", "字号", "textAlign", "对齐方式");

    /** 独立严格解析，避免全局 Jackson 的宽松转换把数字或重复字段悄悄接受。 */
    public static StructuredEditIntent parse(String json) {
        if (json == null || json.length() > 2048) throw invalid();
        try {
            JsonNode root = STRICT_MAPPER.readTree(json);
            if (root == null || !root.isObject() || root.size() != FIELDS.size()) throw invalid();
            var names = root.fieldNames();
            while (names.hasNext()) {
                String name = names.next();
                if (!FIELDS.contains(name) || !root.get(name).isTextual()) throw invalid();
            }
            var intent = new StructuredEditIntent(root.get("version").textValue(), root.get("selector").textValue(),
                    root.get("property").textValue(), root.get("value").textValue());
            intent.validate();
            return intent;
        } catch (IOException error) {
            // 不保留含请求正文的解析异常，避免通过业务日志泄露结构化输入。
            throw invalid();
        }
    }

    /** 服务层再次校验，防止绕过控制器构造 DTO 或附加自由需求获得快捷路径。 */
    public void validateMessage(String message) {
        validate();
        if (!canonicalMessage().equals(message)) throw invalid();
    }

    public String canonicalMessage() {
        validate();
        return "请只修改选中元素的" + LABELS.get(property) + "为“" + value + "”。\n"
                + "元素定位：" + selector + "\n"
                + "保留原有功能、文字、图片和操作方式，其余内容不变。请先确认定位唯一，无法唯一定位时不要修改。";
    }

    private void validate() {
        if (!"1".equals(version) || selector == null || selector.length() > 512
                || !SELECTOR.matcher(selector).matches() || property == null || value == null) throw invalid();
        // 根 body 之外不接受不可编辑节点，路径祖先也不能借此绕过标签边界。
        for (String segment : selector.substring(7).split(" > ")) {
            if (FORBIDDEN_TAGS.contains(segment.substring(0, segment.indexOf(':')))) throw invalid();
        }
        boolean validValue = switch (property) {
            case "color", "backgroundColor" -> value.matches("#[0-9a-fA-F]{6}");
            case "fontSize" -> value.matches("(?:[1-9]|[1-8][0-9]|9[0-6])px");
            case "textAlign" -> Set.of("left", "center", "right").contains(value);
            default -> false;
        };
        if (!validValue) throw invalid();
    }

    private static BusinessException invalid() {
        return new BusinessException(ErrorCode.PARAMS_ERROR, "结构化编辑参数无效或与用户消息不一致");
    }
}
