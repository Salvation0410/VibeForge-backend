package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;

import java.io.IOException;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.fail;

public final class InternalAiToolSchemaAssertions {
    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();
    private static final JsonSchemaFactory SCHEMA_FACTORY =
            JsonSchemaFactory.getInstance(SpecVersion.VersionFlag.V202012);
    private static final JsonNode CONTRACT = loadContract();

    private InternalAiToolSchemaAssertions() {
    }

    public static JsonNode contract() {
        return CONTRACT;
    }

    public static JsonNode tool(String name) {
        for (JsonNode tool : CONTRACT.path("tools")) {
            if (name.equals(tool.path("name").asText())) {
                return tool;
            }
        }
        throw new AssertionError("Missing internal AI tool contract entry: " + name);
    }

    public static void assertRequestValid(String name, Map<String, Object> request) {
        assertValid(name, "request", tool(name).path("requestSchema"), request);
    }

    public static void assertResponseValid(String name, Map<String, Object> response) {
        assertValid(name, "response", tool(name).path("responseSchema"), response);
    }

    public static Map<String, Object> validRequestExample(String name) {
        return switch (name) {
            case "dir_read" -> Map.of("relativeDirPath", "", "codeGenType", "VUE_PROJECT");
            case "file_read", "file_delete" ->
                    Map.of("relativeFilePath", "src/App.vue", "codeGenType", "VUE_PROJECT");
            case "file_write" -> Map.of(
                    "relativeFilePath", "src/App.vue", "content", "<template />", "codeGenType", "VUE_PROJECT");
            case "file_modify" -> Map.of(
                    "relativeFilePath", "src/App.vue", "oldContent", "before", "newContent", "after",
                    "codeGenType", "VUE_PROJECT");
            case "artifact_context", "project_build" -> Map.of("codeGenType", "HTML");
            case "artifact_validate" -> Map.of("artifact", "<html></html>", "codeGenType", "HTML");
            case "artifact_publish" -> Map.of(
                    "artifact", "<html></html>", "codeGenType", "HTML", "engine", "langgraph",
                    "finishReason", "STOP");
            default -> throw new AssertionError("Missing request example for internal AI tool: " + name);
        };
    }

    public static Map<String, Object> validResponseExample(String name) {
        return switch (name) {
            case "dir_read" -> Map.of("entries", List.of("src/App.vue"));
            case "file_read" -> Map.of("content", "<template />");
            case "file_write" -> Map.of("ok", true, "path", "App.vue");
            case "file_modify", "file_delete" -> Map.of("ok", true);
            case "artifact_context" -> Map.of("exists", false, "codeGenType", "HTML");
            case "artifact_validate" -> Map.of("valid", true, "errors", List.of());
            case "artifact_publish" -> Map.of(
                    "published", true, "versionId", "v1", "hashes", Map.of("index.html", "sha256"));
            case "project_build" -> Map.of("built", true, "errorCode", "", "message", "");
            default -> throw new AssertionError("Missing response example for internal AI tool: " + name);
        };
    }

    private static void assertValid(String name, String field, JsonNode schemaNode, Map<String, Object> value) {
        JsonSchema schema = SCHEMA_FACTORY.getSchema(schemaNode);
        Set<ValidationMessage> errors = schema.validate(OBJECT_MAPPER.valueToTree(value));
        if (!errors.isEmpty()) {
            fail("Invalid " + name + " " + field + ": " + errors);
        }
    }

    private static JsonNode loadContract() {
        Path path = Path.of("ai-service", "src", "ai_service", "contracts", "internal-ai-tools-v1.json");
        try {
            return OBJECT_MAPPER.readTree(path.toFile());
        } catch (IOException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }
}
