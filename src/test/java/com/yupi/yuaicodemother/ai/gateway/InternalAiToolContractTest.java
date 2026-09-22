package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.nio.file.Path;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

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
                Map.entry("project_build", InternalAiTool.PROJECT_BUILD)
        );

        expected.forEach((name, tool) -> assertEquals(tool, InternalAiTool.fromExternalName(name)));
        assertThrows(IllegalArgumentException.class,
                () -> InternalAiTool.fromExternalName("search_reference"));
    }

    @Test
    void matchesThePackagedPythonContract() throws Exception {
        var contractPath = Path.of("ai-service", "src", "ai_service", "contracts", "internal-ai-tools-v1.json");
        var tools = new ObjectMapper().readTree(contractPath.toFile()).path("tools");
        Set<String> contractNames = new HashSet<>();

        for (var toolNode : tools) {
            String name = toolNode.path("name").asText();
            contractNames.add(name);
            InternalAiTool tool = InternalAiTool.fromExternalName(name);
            assertEquals(toolNode.path("modelCallable").asBoolean(), tool.modelCallable(), name);
            Set<String> aliases = new HashSet<>();
            toolNode.path("aliases").forEach(alias -> aliases.add(alias.asText()));
            assertEquals(aliases, tool.aliases(), name);
        }

        Set<String> javaNames = new HashSet<>();
        for (InternalAiTool tool : InternalAiTool.values()) {
            javaNames.add(tool.canonicalName());
        }
        assertEquals(contractNames, javaNames);
    }
}
