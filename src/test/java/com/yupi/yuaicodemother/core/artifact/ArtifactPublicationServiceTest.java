package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

class ArtifactPublicationServiceTest {
    @TempDir Path root;

    @Test
    void publishesIdempotentlyAndResolvesCurrentVersion() throws Exception {
        var fixture = fixture();
        var first = fixture.service.publishMultiFile(42, "req-1", artifact("blue"), "legacy", "STOP");
        var second = fixture.service.publishMultiFile(42, "req-1", artifact("blue"), "legacy", "STOP");
        Path active = fixture.resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42);
        assertEquals("req-1", first.versionId()); assertEquals(first.hashes(), second.hashes());
        assertEquals("main { color: blue; }", Files.readString(active.resolve("style.css")));
    }

    @Test
    void conflictingRequestIdIsRejectedWithoutChangingCurrent() {
        var fixture = fixture();
        fixture.service.publishMultiFile(42, "req-1", artifact("blue"), "legacy", "STOP");
        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishMultiFile(42, "req-1", artifact("red"), "legacy", "STOP"));
        assertEquals("ARTIFACT_VERSION_CONFLICT", error.getErrorCode());
        assertTrue(fixture.resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42).endsWith("req-1"));
    }

    @Test
    void tamperedReleaseIsRejectedOnIdempotentRetry() throws Exception {
        var fixture = fixture();
        fixture.service.publishMultiFile(42, "req-1", artifact("blue"), "legacy", "STOP");
        Files.writeString(root.resolve("multi_file_42/.releases/req-1/style.css"), "main { color: red; }");

        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishMultiFile(42, "req-1", artifact("blue"), "legacy", "STOP"));

        assertEquals("ARTIFACT_VERSION_CONFLICT", error.getErrorCode());
    }

    @Test
    void incompleteReleaseIsRejectedOnIdempotentRetry() throws Exception {
        var fixture = fixture();
        fixture.service.publishMultiFile(42, "req-1", artifact("blue"), "legacy", "STOP");
        Files.delete(root.resolve("multi_file_42/.releases/req-1/script.js"));

        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishMultiFile(42, "req-1", artifact("blue"), "legacy", "STOP"));

        assertEquals("ARTIFACT_VERSION_CONFLICT", error.getErrorCode());
    }

    @Test
    void existingCompleteReleaseCanRecoverPointerSwitch() throws Exception {
        var fixture = fixture();
        fixture.service.publishMultiFile(42, "req-old", artifact("blue"), "legacy", "STOP");
        fixture.service.publishMultiFile(42, "req-retry", artifact("green"), "legacy", "STOP");
        Path appRoot = root.resolve("multi_file_42");
        Files.delete(appRoot.resolve(".published/req-retry.json"));
        Files.writeString(appRoot.resolve(".current"), "req-old");

        fixture.service.publishMultiFile(42, "req-retry", artifact("green"), "legacy", "STOP");

        assertEquals("req-retry", Files.readString(appRoot.resolve(".current")).trim());
        assertTrue(Files.isRegularFile(appRoot.resolve(".published/req-retry.json")));
        assertEquals("main { color: green; }", Files.readString(
                fixture.resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42).resolve("style.css")));
    }

    @Test
    void olderExistingReleaseCannotReplaceNewerCurrentVersion() {
        var fixture = fixture();
        fixture.service.publishMultiFile(42, "req-old", artifact("blue"), "legacy", "STOP");
        fixture.service.publishMultiFile(42, "req-new", artifact("green"), "legacy", "STOP");

        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishMultiFile(42, "req-old", artifact("blue"), "legacy", "STOP"));

        assertEquals("ARTIFACT_VERSION_CONFLICT", error.getErrorCode());
        assertTrue(fixture.resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42).endsWith("req-new"));
    }

    @Test
    void retainsCurrentAndTwoPreviousVersions() throws Exception {
        var fixture = fixture();
        for (int i=1;i<=5;i++) { fixture.service.publishMultiFile(42, "req-"+i, artifact("c"+i), "legacy", "STOP"); Thread.sleep(3); }
        try (var versions=Files.list(root.resolve("multi_file_42/.releases"))) { assertEquals(3, versions.count()); }
        assertTrue(fixture.resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42).endsWith("req-5"));
    }

    @Test
    void retainedTombstonePreventsCleanedRequestFromRollingBackCurrent() throws Exception {
        var fixture = fixture();
        for (int i = 1; i <= 5; i++) {
            fixture.service.publishMultiFile(42, "req-" + i, artifact("c" + i), "legacy", "STOP");
        }
        Path appRoot = root.resolve("multi_file_42");
        assertFalse(Files.exists(appRoot.resolve(".releases/req-1")));
        assertTrue(Files.isRegularFile(appRoot.resolve(".published/req-1.json")));

        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishMultiFile(42, "req-1", artifact("c1"), "legacy", "STOP"));

        assertEquals("ARTIFACT_VERSION_CONFLICT", error.getErrorCode());
        assertEquals("req-5", Files.readString(appRoot.resolve(".current")).trim());
        assertFalse(Files.exists(appRoot.resolve(".releases/req-1")));
    }

    @Test
    void recoversPointerBySequenceInsteadOfCreatedAt() throws Exception {
        var fixture = fixture();
        fixture.service.publishMultiFile(42, "req-old", artifact("blue"), "legacy", "STOP");
        fixture.service.publishMultiFile(42, "req-retry", artifact("green"), "legacy", "STOP");
        Path appRoot = root.resolve("multi_file_42");
        rewriteCreatedAt(appRoot.resolve(".releases/req-old/manifest.json"), "2099-01-01T00:00:00Z");
        rewriteCreatedAt(appRoot.resolve(".releases/req-retry/manifest.json"), "2000-01-01T00:00:00Z");
        Files.writeString(appRoot.resolve(".current"), "req-old");

        fixture.service.publishMultiFile(42, "req-retry", artifact("green"), "legacy", "STOP");

        assertEquals("req-retry", Files.readString(appRoot.resolve(".current")).trim());
    }

    @Test
    void legacyManifestWithoutSequenceCannotRecoverOverAnotherCurrentVersion() throws Exception {
        var fixture = fixture();
        fixture.service.publishMultiFile(42, "req-old", artifact("blue"), "legacy", "STOP");
        fixture.service.publishMultiFile(42, "req-target", artifact("green"), "legacy", "STOP");
        Path appRoot = root.resolve("multi_file_42");
        Path targetManifest = appRoot.resolve(".releases/req-target/manifest.json");
        var legacyManifest = fixture.mapper.readTree(targetManifest.toFile());
        ((com.fasterxml.jackson.databind.node.ObjectNode) legacyManifest).remove("sequence");
        fixture.mapper.writeValue(targetManifest.toFile(), legacyManifest);
        Files.writeString(appRoot.resolve(".current"), "req-old");

        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishMultiFile(42, "req-target", artifact("green"), "legacy", "STOP"));

        assertEquals("ARTIFACT_VERSION_CONFLICT", error.getErrorCode());
        assertEquals("req-old", Files.readString(appRoot.resolve(".current")).trim());
    }

    @Test
    void fallsBackToLegacyFlatRoot() throws Exception {
        Path legacy = root.resolve("multi_file_42"); Files.createDirectories(legacy); Files.writeString(legacy.resolve("index.html"), "legacy");
        assertEquals(legacy.toAbsolutePath(), fixture().resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42));
    }

    private Fixture fixture() {
        ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule());
        var resolver = new ArtifactPathResolver(root, mapper);
        return new Fixture(resolver, new ArtifactPublicationService(new MultiFileArtifactValidator(), resolver, mapper), mapper);
    }

    /** 仅反转审计时间，验证恢复顺序完全不依赖 createdAt。 */
    private void rewriteCreatedAt(Path manifestPath, String createdAt) throws Exception {
        ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule());
        var manifest = mapper.readTree(manifestPath.toFile());
        ((com.fasterxml.jackson.databind.node.ObjectNode) manifest).put("createdAt", createdAt);
        mapper.writeValue(manifestPath.toFile(), manifest);
    }

    private String artifact(String color) {
        return "index.html\n```html\n<!doctype html><html><head><link rel=\"stylesheet\" href=\"style.css\"></head><body><main>x</main><script src=\"script.js\"></script></body></html>\n```\n\n"
                + "style.css\n```css\nmain { color: "+color+"; }\n```\n\nscript.js\n```javascript\ndocument.querySelector('main');\n```";
    }
    private record Fixture(ArtifactPathResolver resolver, ArtifactPublicationService service, ObjectMapper mapper) { }
}
