package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.attribute.FileTime;

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
    void fallbackUsesManifestSequenceAndRejectsTamperedHash() throws Exception {
        var resolver = resolver();
        var publisher = new ArtifactPublicationService(new MultiFileArtifactValidator(), resolver, mapper());
        publisher.publishMultiFile(42, "req-1", artifact("blue"), "legacy", "STOP");
        publisher.publishMultiFile(42, "req-2", artifact("green"), "legacy", "STOP");
        Path appRoot = root.resolve("multi_file_42");
        Files.writeString(appRoot.resolve(".current"), "missing-version");
        Files.setLastModifiedTime(appRoot.resolve(".releases/req-1"), FileTime.fromMillis(Long.MAX_VALUE / 2));

        assertTrue(resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42).endsWith("req-2"));

        Files.writeString(appRoot.resolve(".releases/req-2/style.css"), "tampered");
        assertTrue(resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42).endsWith("req-1"));
    }

    @Test
    void htmlReleaseAcceptsIndexOnlyButMultiFileRequiresExactlyThreeFiles() throws Exception {
        var resolver = resolver();
        var publisher = new ArtifactPublicationService(new MultiFileArtifactValidator(), resolver, mapper());
        publisher.publishHtml(7, "html-1", html(), "legacy", "STOP");
        assertTrue(resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 7).endsWith("html-1"));

        publisher.publishMultiFile(42, "multi-1", artifact("blue"), "legacy", "STOP");
        Path manifestPath = root.resolve("multi_file_42/.releases/multi-1/manifest.json");
        var manifest = mapper().readTree(manifestPath.toFile());
        ((com.fasterxml.jackson.databind.node.ObjectNode) manifest.get("hashes")).remove("script.js");
        mapper().writeValue(manifestPath.toFile(), manifest);
        assertEquals(root.resolve("multi_file_42").toAbsolutePath(),
                resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42));
    }

    @Test
    void resolverRejectsNonPositiveSequenceAndManifestIdentityMismatch() throws Exception {
        var resolver = resolver();
        var publisher = new ArtifactPublicationService(new MultiFileArtifactValidator(), resolver, mapper());
        publisher.publishHtml(7, "html-1", html(), "legacy", "STOP");
        Path appRoot = root.resolve("html_7");
        Path manifestPath = appRoot.resolve(".releases/html-1/manifest.json");
        var manifest = mapper().readTree(manifestPath.toFile());
        ((com.fasterxml.jackson.databind.node.ObjectNode) manifest).put("sequence", 0);
        mapper().writeValue(manifestPath.toFile(), manifest);
        assertEquals(appRoot.toAbsolutePath(), resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 7));

        ((com.fasterxml.jackson.databind.node.ObjectNode) manifest).put("sequence", 1);
        ((com.fasterxml.jackson.databind.node.ObjectNode) manifest).put("requestId", "other");
        mapper().writeValue(manifestPath.toFile(), manifest);
        assertEquals(appRoot.toAbsolutePath(), resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 7));

        ((com.fasterxml.jackson.databind.node.ObjectNode) manifest).put("requestId", "html-1");
        ((com.fasterxml.jackson.databind.node.ObjectNode) manifest).put("appId", 8);
        mapper().writeValue(manifestPath.toFile(), manifest);
        assertEquals(appRoot.toAbsolutePath(), resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 7));
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

    private String html() {
        return "```html\n<!doctype html><html><head></head><body>ok</body></html>\n```";
    }
}
