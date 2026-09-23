package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.networknt.schema.JsonSchema;
import com.networknt.schema.JsonSchemaFactory;
import com.networknt.schema.SpecVersion;
import com.networknt.schema.ValidationMessage;

import java.io.IOException;
import java.net.URISyntaxException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.fail;

public final class InternalAiToolSchemaAssertions {
    private static final Path CONTRACT_RELATIVE_PATH = Path.of(
            "ai-service", "src", "ai_service", "contracts", "internal-ai-tools-v1.json");
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
            if (tool.path("name").asText().equals(name)) {
                return tool;
            }
        }
        throw new AssertionError("Missing internal AI tool contract entry: " + name);
    }

    public static void assertRequestValid(String name, Map<String, Object> request) {
        assertValidSchemaNode(name, "requestSchema", tool(name).get("requestSchema"), request);
    }

    public static void assertResponseValid(String name, Map<String, Object> response) {
        assertValidSchemaNode(name, "responseSchema", tool(name).get("responseSchema"), response);
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

    static void assertValidSchemaNode(
            String name, String field, JsonNode schemaNode, Map<String, Object> value) {
        if (schemaNode == null || schemaNode.isMissingNode() || schemaNode.isNull()
                || (!schemaNode.isObject() && !schemaNode.isBoolean())) {
            throw new AssertionError("Invalid JSON Schema node for tool " + name + " field " + field);
        }
        JsonSchema schema = SCHEMA_FACTORY.getSchema(schemaNode);
        Set<ValidationMessage> errors = schema.validate(OBJECT_MAPPER.valueToTree(value));
        if (!errors.isEmpty()) {
            fail("Invalid " + name + " " + field + ": " + errors);
        }
    }

    private static JsonNode loadContract() {
        try {
            return OBJECT_MAPPER.readTree(locateContractPath().toFile());
        } catch (IOException | RuntimeException exception) {
            throw new ExceptionInInitializerError(exception);
        }
    }

    private static Path locateContractPath() {
        Set<Path> roots = new LinkedHashSet<>();
        addAncestors(roots, Path.of(System.getProperty("user.dir")).toAbsolutePath().normalize());
        Path contractPath = findContract(roots);
        if (contractPath != null) {
            return contractPath;
        }

        try {
            Path codeSource = Path.of(InternalAiToolSchemaAssertions.class.getProtectionDomain()
                    .getCodeSource().getLocation().toURI()).toAbsolutePath().normalize();
            addAncestors(roots, codeSource);
        } catch (URISyntaxException | NullPointerException exception) {
            throw new IllegalStateException(
                    "Unable to resolve internal AI tool contract code source; attempted roots: " + roots,
                    exception);
        }

        contractPath = findContract(roots);
        if (contractPath != null) {
            return contractPath;
        }
        throw new IllegalStateException(
                "Internal AI tool contract not found; attempted roots: " + roots);
    }

    private static Path findContract(Set<Path> roots) {
        for (Path root : roots) {
            Path candidate = root.resolve(CONTRACT_RELATIVE_PATH).normalize();
            if (Files.isRegularFile(candidate)) {
                return candidate;
            }
        }
        return null;
    }

    private static void addAncestors(Set<Path> roots, Path start) {
        for (Path current = start; current != null; current = current.getParent()) {
            roots.add(current);
        }
    }
}
