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
    void retainsCurrentAndTwoPreviousVersions() throws Exception {
        var fixture = fixture();
        for (int i=1;i<=5;i++) { fixture.service.publishMultiFile(42, "req-"+i, artifact("c"+i), "legacy", "STOP"); Thread.sleep(3); }
        try (var versions=Files.list(root.resolve("multi_file_42/.releases"))) { assertEquals(3, versions.count()); }
        assertTrue(fixture.resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42).endsWith("req-5"));
    }

    @Test
    void fallsBackToLegacyFlatRoot() throws Exception {
        Path legacy = root.resolve("multi_file_42"); Files.createDirectories(legacy); Files.writeString(legacy.resolve("index.html"), "legacy");
        assertEquals(legacy.toAbsolutePath(), fixture().resolver.resolveActiveRoot(CodeGenTypeEnum.MULTI_FILE, 42));
    }

    private Fixture fixture() {
        ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule());
        var resolver = new ArtifactPathResolver(root, mapper);
        return new Fixture(resolver, new ArtifactPublicationService(new MultiFileArtifactValidator(), resolver, mapper));
    }

    private String artifact(String color) {
        return "index.html\n```html\n<!doctype html><html><head><link rel=\"stylesheet\" href=\"style.css\"></head><body><main>x</main><script src=\"script.js\"></script></body></html>\n```\n\n"
                + "style.css\n```css\nmain { color: "+color+"; }\n```\n\nscript.js\n```javascript\ndocument.querySelector('main');\n```";
    }
    private record Fixture(ArtifactPathResolver resolver, ArtifactPublicationService service) { }
}
