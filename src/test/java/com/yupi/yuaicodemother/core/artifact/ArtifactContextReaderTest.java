package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ArtifactContextReaderTest {
    @TempDir
    Path root;

    @Test
    void returnsAbsentContextWhenActiveRootDoesNotExist() {
        var reader = reader();

        assertEquals(Map.of("exists", false, "codeGenType", "HTML"),
                reader.read(CodeGenTypeEnum.HTML, 42L));
    }

    @Test
    void rebuildsHtmlAndMultiFilePromptProtocols() throws Exception {
        Files.createDirectories(root.resolve("html_7"));
        Files.writeString(root.resolve("html_7/index.html"), "<!doctype html><html><body>ready</body></html>");
        Files.createDirectories(root.resolve("multi_file_8"));
        Files.writeString(root.resolve("multi_file_8/index.html"), "<main>ready</main>");
        Files.writeString(root.resolve("multi_file_8/style.css"), "main { color: blue; }");
        Files.writeString(root.resolve("multi_file_8/script.js"), "document.querySelector('main');");

        Map<String, Object> html = reader().read(CodeGenTypeEnum.HTML, 7L);
        assertEquals(true, html.get("exists"));
        assertEquals("index.html", html.get("entry"));
        assertEquals("```html\n<!doctype html><html><body>ready</body></html>\n```", html.get("artifact"));

        Map<String, Object> multi = reader().read(CodeGenTypeEnum.MULTI_FILE, 8L);
        assertEquals(true, multi.get("exists"));
        assertEquals("""
                index.html
                ```html
                <main>ready</main>
                ```
                style.css
                ```css
                main { color: blue; }
                ```
                script.js
                ```javascript
                document.querySelector('main');
                ```""", multi.get("artifact"));
    }

    @Test
    void listsBoundedVueSourcesWithoutGeneratedOrHiddenFiles() throws Exception {
        Path project = root.resolve("vue_project_9");
        Files.createDirectories(project.resolve("src/components"));
        Files.createDirectories(project.resolve("node_modules/pkg"));
        Files.createDirectories(project.resolve("dist/assets"));
        Files.createDirectories(project.resolve(".git"));
        Files.writeString(project.resolve("package.json"), "{}");
        Files.writeString(project.resolve("src/App.vue"), "<template />");
        Files.writeString(project.resolve("src/components/Card.vue"), "<template />");
        Files.writeString(project.resolve("node_modules/pkg/index.js"), "ignored");
        Files.writeString(project.resolve("dist/index.html"), "ignored");
        Files.writeString(project.resolve(".git/config"), "ignored");

        Map<String, Object> context = reader().read(CodeGenTypeEnum.VUE_PROJECT, 9L);

        assertEquals(true, context.get("exists"));
        assertEquals(List.of("package.json", "src/App.vue", "src/components/Card.vue"), context.get("entries"));
        assertEquals(false, context.get("truncated"));
    }

    @Test
    void capsVueEntriesAndRejectsOversizedStaticArtifact() throws Exception {
        Path project = root.resolve("vue_project_10/src");
        Files.createDirectories(project);
        for (int index = 0; index < 205; index++) {
            Files.writeString(project.resolve("File%03d.vue".formatted(index)), "<template />");
        }

        Map<String, Object> vue = reader().read(CodeGenTypeEnum.VUE_PROJECT, 10L);
        assertEquals(200, ((List<?>) vue.get("entries")).size());
        assertEquals(true, vue.get("truncated"));

        Files.createDirectories(root.resolve("html_11"));
        Files.writeString(root.resolve("html_11/index.html"), "x".repeat(100_001));
        BusinessException error = assertThrows(BusinessException.class,
                () -> reader().read(CodeGenTypeEnum.HTML, 11L));
        assertTrue(error.getMessage().startsWith("ARTIFACT_CONTEXT_TOO_LARGE:"));
    }

    private ArtifactContextReader reader() {
        return new ArtifactContextReader(new ArtifactPathResolver(root, new ObjectMapper()));
    }
}
