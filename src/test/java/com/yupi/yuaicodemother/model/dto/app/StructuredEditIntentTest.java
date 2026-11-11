package com.yupi.yuaicodemother.model.dto.app;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.exception.BusinessException;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

class StructuredEditIntentTest {
    private final ObjectMapper mapper = new ObjectMapper();
    private static final String SELECTOR = "body > main:nth-of-type(1) > p:nth-of-type(2)";

    @Test
    void sharedCrossServiceFixturesHaveIdenticalCanonicalMessagesAndValidation() throws Exception {
        var fixtures = mapper.readTree(Path.of("ai-service/tests/fixtures/structured-edit-v1.json").toFile());
        for (var sample : fixtures.get("valid")) {
            var intent = StructuredEditIntent.parse(sample.get("intent").toString());
            assertEquals(sample.get("message").asText(), intent.canonicalMessage());
            intent.validateMessage(sample.get("message").asText());
        }
        for (var sample : fixtures.get("invalid")) {
            assertThrows(BusinessException.class,
                    () -> StructuredEditIntent.parse(sample.get("intent").toString()), sample.get("name").asText());
        }
    }

    @Test
    void canonicalMessagePreservesValueAndRejectsAnyExtraRequirement() {
        var intent = new StructuredEditIntent("1", SELECTOR, "color", "#Aa12FF");
        String expected = "请只修改选中元素的文字颜色为“#Aa12FF”。\n元素定位：" + SELECTOR
                + "\n保留原有功能、文字、图片和操作方式，其余内容不变。请先确认定位唯一，无法唯一定位时不要修改。";
        assertEquals(expected, intent.canonicalMessage());
        intent.validateMessage(expected);
        for (String modified : List.of(expected + "\n", expected + "顺便删除图片", expected.replace("\n", "\r\n"))) {
            assertThrows(BusinessException.class, () -> intent.validateMessage(modified));
        }
    }

    @Test
    void rejectsUnknownMissingNonStringDuplicateAndTrailingJson() throws Exception {
        String valid = mapper.writeValueAsString(Map.of("version", "1", "selector", SELECTOR,
                "property", "fontSize", "value", "96px"));
        assertEquals("96px", StructuredEditIntent.parse(valid).value());
        for (String invalid : List.of("null", "[]", "", "{}", valid + " {}", " ".repeat(2049),
                valid.replace("\"version\":\"1\"", "\"version\":1"),
                valid.replace("\"version\":\"1\"", "\"version\":true"),
                valid.replace("\"version\":\"1\"", "\"version\":null"),
                valid.replace("\"version\":\"1\"", "\"version\":[]"),
                valid.replace("\"version\":\"1\"", "\"version\":{}"),
                valid.replace("\"version\":\"1\"", "\"version\":\"1\",\"version\":\"1\""),
                valid.replace("\"version\":\"1\"", "\"unknown\":\"1\""),
                valid.substring(0, valid.length() - 1) + ",\"unknown\":\"x\"}")) {
            assertThrows(BusinessException.class, () -> StructuredEditIntent.parse(invalid), invalid);
        }
    }

    @Test
    void rejectsUnboundedOrUnsafeSelectorsAndProperties() {
        for (String selector : List.of("body", "html", "#id", "body > p:nth-of-type(0)",
                "body > p:nth-of-type(01)", "body > p:nth-of-type(10000)", "body > P:nth-of-type(1)",
                "body > p:nth-of-type(1), script", "body > script:nth-of-type(1)",
                "body" + " > div:nth-of-type(1)".repeat(13),
                "body" + (" > " + "a".repeat(31) + ":nth-of-type(9999)").repeat(12))) {
            assertThrows(BusinessException.class,
                    () -> new StructuredEditIntent("1", selector, "color", "#123456").canonicalMessage(), selector);
        }
        for (var propertyValue : List.of(Map.entry("color", "red"), Map.entry("backgroundColor", "#fff"),
                Map.entry("color", "#123456;display:none"), Map.entry("fontSize", "0px"),
                Map.entry("fontSize", "97px"), Map.entry("fontSize", "01px"), Map.entry("fontSize", "1.5px"),
                Map.entry("textAlign", "justify"), Map.entry("display", "none"))) {
            assertThrows(BusinessException.class, () -> new StructuredEditIntent("1", SELECTOR,
                    propertyValue.getKey(), propertyValue.getValue()).canonicalMessage());
        }
        assertThrows(BusinessException.class,
                () -> new StructuredEditIntent("2", SELECTOR, "color", "#123456").canonicalMessage());
    }
}
