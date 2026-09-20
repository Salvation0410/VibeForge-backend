package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

class ArtifactPathResolverTest {
    @TempDir Path root;

    @Test
    void nonMultiFileTypesKeepDirectRoots() {
        var resolver = resolver();
        assertEquals(root.resolve("html_1").toAbsolutePath(), resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 1));
        assertEquals(root.resolve("vue_project_2").toAbsolutePath(),
                resolver.resolveActiveRoot(CodeGenTypeEnum.VUE_PROJECT, 2));
    }

    @Test
    void malformedDirectoryNamesAreRejected() {
        var resolver = resolver();
        assertThrows(IllegalArgumentException.class, () -> resolver.resolveDirectoryName("../multi_file_42"));
        assertThrows(IllegalArgumentException.class, () -> resolver.resolveDirectoryName("multi_file_x"));
    }

    @Test
    void corruptPointerFallsBackToNewestCompleteRelease() throws Exception {
        var resolver = resolver();
        var publisher = new ArtifactPublicationService(new MultiFileArtifactValidator(), resolver, mapper());
        publisher.publishMultiFile(42, "req-1", artifact("blue"), "legacy", "STOP");
        Thread.sleep(3);
        publisher.publishMultiFile(42, "req-2", artifact("green"), "legacy", "STOP");
        Files.writeString(root.resolve("multi_file_42/.current"), "missing-version");

        assertTrue(resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42).endsWith("req-2"));
    }

    @Test
    void absentPointerUsesLegacyFlatRootWithoutActivatingRelease() throws Exception {
        var resolver = resolver();
        Path legacyRoot = root.resolve("multi_file_42");
        Files.createDirectories(legacyRoot.resolve(".releases/orphan"));

        assertEquals(legacyRoot.toAbsolutePath(), resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42));
        assertFalse(Files.exists(legacyRoot.resolve(".current")));
    }

    private ArtifactPathResolver resolver() {
        return new ArtifactPathResolver(root, mapper());
    }

    private ObjectMapper mapper() {
        return new ObjectMapper().registerModule(new JavaTimeModule());
    }

    private String artifact(String color) {
        return "index.html\n```html\n<!doctype html><html><head><link rel=\"stylesheet\" href=\"style.css\"></head><body><main>x</main><script src=\"script.js\"></script></body></html>\n```\n\n"
                + "style.css\n```css\nmain { color: " + color + "; }\n```\n\nscript.js\n```javascript\ndocument.querySelector('main');\n```";
    }
}
