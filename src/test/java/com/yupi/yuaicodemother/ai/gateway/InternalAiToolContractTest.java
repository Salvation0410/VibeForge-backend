package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.node.MissingNode;
import com.fasterxml.jackson.databind.node.NullNode;
import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class InternalAiToolContractTest {

    @Test
    void resolvesCanonicalNamesAndHistoricalAliases() {
        Map<String, InternalAiTool> expected = Map.ofEntries(
                Map.entry("dir_read", InternalAiTool.DIR_READ),
                Map.entry("readDir", InternalAiTool.DIR_READ),
                Map.entry("read_dir", InternalAiTool.DIR_READ),
                Map.entry("file_read", InternalAiTool.FILE_READ),
                Map.entry("readFile", InternalAiTool.FILE_READ),
                Map.entry("read_file", InternalAiTool.FILE_READ),
                Map.entry("file_write", InternalAiTool.FILE_WRITE),
                Map.entry("writeFile", InternalAiTool.FILE_WRITE),
                Map.entry("write_file", InternalAiTool.FILE_WRITE),
                Map.entry("file_modify", InternalAiTool.FILE_MODIFY),
                Map.entry("modifyFile", InternalAiTool.FILE_MODIFY),
                Map.entry("modify_file", InternalAiTool.FILE_MODIFY),
                Map.entry("file_delete", InternalAiTool.FILE_DELETE),
                Map.entry("deleteFile", InternalAiTool.FILE_DELETE),
                Map.entry("delete_file", InternalAiTool.FILE_DELETE),
                Map.entry("artifact_validate", InternalAiTool.ARTIFACT_VALIDATE),
                Map.entry("artifact_validation", InternalAiTool.ARTIFACT_VALIDATE),
                Map.entry("artifact_context", InternalAiTool.ARTIFACT_CONTEXT),
                Map.entry("artifact_publish", InternalAiTool.ARTIFACT_PUBLISH),
                Map.entry("project_build", InternalAiTool.PROJECT_BUILD),
                Map.entry("vue_source_snapshot", InternalAiTool.VUE_SOURCE_SNAPSHOT)
        );

        expected.forEach((name, tool) -> assertEquals(tool, InternalAiTool.fromExternalName(name)));
        assertThrows(IllegalArgumentException.class,
                () -> InternalAiTool.fromExternalName("search_reference"));
    }

    @Test
    void matchesThePackagedPythonContract() {
        var contract = InternalAiToolSchemaAssertions.contract();
        assertEquals("https://json-schema.org/draft/2020-12/schema",
                contract.path("schemaDialect").asText());

        Set<String> contractNames = new HashSet<>();
        Set<String> externalNames = new LinkedHashSet<>();

        for (var toolNode : contract.path("tools")) {
            String name = toolNode.path("name").asText();
            contractNames.add(name);
            assertTrue(externalNames.add(name), "duplicate canonical name: " + name);
            InternalAiTool tool = InternalAiTool.fromExternalName(name);
            assertEquals(toolNode.path("modelCallable").asBoolean(), tool.modelCallable(), name);
            Set<String> aliases = new HashSet<>();
            toolNode.path("aliases").forEach(alias -> {
                String aliasName = alias.asText();
                assertTrue(externalNames.add(aliasName), "duplicate external name: " + aliasName);
                aliases.add(aliasName);
            });
            assertEquals(aliases, tool.aliases(), name);
        }

        Set<String> javaNames = new HashSet<>();
        for (InternalAiTool tool : InternalAiTool.values()) {
            String name = tool.canonicalName();
            javaNames.add(name);
            var toolNode = InternalAiToolSchemaAssertions.tool(name);
            assertNotNull(toolNode.get("requestSchema"), name);
            assertNotNull(toolNode.get("responseSchema"), name);
            assertFalse(toolNode.path("requestSchema").isMissingNode(), name);
            assertFalse(toolNode.path("responseSchema").isMissingNode(), name);
            InternalAiToolSchemaAssertions.assertRequestValid(
                    name, InternalAiToolSchemaAssertions.validRequestExample(name));
            InternalAiToolSchemaAssertions.assertResponseValid(
                    name, InternalAiToolSchemaAssertions.validResponseExample(name));
        }
        assertEquals(contractNames, javaNames);
    }

    @Test
    void rejectsUnexpectedRequestFieldsAndAllowsResponseExtensions() {
        Map<String, Object> request = new java.util.HashMap<>(
                InternalAiToolSchemaAssertions.validRequestExample("project_build"));
        request.put("appId", 42L);
        assertThrows(AssertionError.class,
                () -> InternalAiToolSchemaAssertions.assertRequestValid("project_build", request));

        Map<String, Object> response = new java.util.HashMap<>(
                InternalAiToolSchemaAssertions.validResponseExample("project_build"));
        response.put("durationMs", 123L);
        InternalAiToolSchemaAssertions.assertResponseValid("project_build", response);
    }

    @Test
    void rejectsVueSourceSnapshotFileContentOverLimit() {
        Map<String, Object> response = Map.of(
                "files", java.util.List.of(Map.of(
                        "path", "src/App.vue", "content", "x".repeat(12_001), "truncated", true)),
                "eligibleFileCount", 1,
                "includedFileCount", 1,
                "omittedFileCount", 0,
                "truncated", true);

        assertThrows(AssertionError.class,
                () -> InternalAiToolSchemaAssertions.assertResponseValid("vue_source_snapshot", response));
    }

    @Test
    void rejectsMissingSchemasAndUnknownToolsInsteadOfSilentlyPassing() {
        AssertionError missingSchema = assertThrows(AssertionError.class,
                () -> InternalAiToolSchemaAssertions.assertValidSchemaNode(
                        "project_build", "requestSchema", MissingNode.getInstance(), Map.of()));
        assertTrue(missingSchema.getMessage().contains("project_build"));
        assertTrue(missingSchema.getMessage().contains("requestSchema"));

        AssertionError nullSchema = assertThrows(AssertionError.class,
                () -> InternalAiToolSchemaAssertions.assertValidSchemaNode(
                        "project_build", "responseSchema", NullNode.getInstance(), Map.of()));
        assertTrue(nullSchema.getMessage().contains("project_build"));
        assertTrue(nullSchema.getMessage().contains("responseSchema"));

        AssertionError unknownTool = assertThrows(AssertionError.class,
                () -> InternalAiToolSchemaAssertions.tool("unknown_tool"));
        assertTrue(unknownTool.getMessage().contains("unknown_tool"));
    }
}
