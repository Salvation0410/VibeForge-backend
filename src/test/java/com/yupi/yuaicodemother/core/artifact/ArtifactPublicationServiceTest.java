package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.yupi.yuaicodemother.config.HtmlArtifactProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.env.MockEnvironment;

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
    void publishesHtmlIdempotentlyAndRequiresOnlyIndexFile() throws Exception {
        var fixture = fixture();
        String html = html("blue");
        var first = fixture.service.publishHtml(7, "html-1", html, "legacy", "STOP");
        var second = fixture.service.publishHtml(7, "html-1", html, "legacy", "STOP");

        Path active = fixture.resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 7);
        assertEquals(first.hashes(), second.hashes());
        assertEquals(java.util.Set.of("index.html"), first.hashes().keySet());
        assertTrue(Files.readString(active.resolve("index.html")).contains("blue"));
        assertFalse(Files.exists(active.resolve("style.css")));
    }

    @Test
    void failedSmokeGatePreservesPreviousHtmlRelease() throws Exception {
        var fixture = fixture();
        fixture.service.publishHtml(7, "html-old", html("blue"), "legacy", "STOP");
        Path appRoot = root.resolve("html_7");
        var properties = new HtmlArtifactProperties();
        HtmlSmokeTester rejected = path -> HtmlSmokeTestResult.failure("HTML_SMOKE_TEST_FAILED", "JavaScript syntax error");
        var guarded = new ArtifactPublicationService(new MultiFileArtifactValidator(), new HtmlArtifactValidator(),
                fixture.resolver, fixture.mapper, null, rejected, properties, new MockEnvironment());

        var error = assertThrows(ArtifactValidationException.class,
                () -> guarded.publishHtml(7, "html-new", html("green"), "legacy", "STOP"));

        assertEquals("HTML_SMOKE_TEST_FAILED", error.getErrorCode());
        assertEquals("html-old", Files.readString(appRoot.resolve(".current")).trim());
        assertFalse(Files.exists(appRoot.resolve(".releases/html-new")));
    }

    @Test
    void requiredSmokeGateCannotBeDisabled() {
        var fixture = fixture();
        var properties = new HtmlArtifactProperties();
        properties.setEnabled(false);
        var guarded = new ArtifactPublicationService(new MultiFileArtifactValidator(), new HtmlArtifactValidator(),
                fixture.resolver, fixture.mapper, null, path -> HtmlSmokeTestResult.success(), properties,
                new MockEnvironment());

        var error = assertThrows(ArtifactValidationException.class,
                () -> guarded.publishHtml(7, "html-new", html("green"), "legacy", "STOP"));
        assertEquals("HTML_SMOKE_TEST_UNAVAILABLE", error.getErrorCode());
    }

    @Test
    void developmentCanExplicitlyDisableOptionalSmokeGate() {
        var fixture = fixture();
        var properties = new HtmlArtifactProperties();
        properties.setEnabled(false);
        properties.setRequired(false);
        var guarded = new ArtifactPublicationService(new MultiFileArtifactValidator(), new HtmlArtifactValidator(),
                fixture.resolver, fixture.mapper, null,
                path -> { throw new AssertionError("禁用后不应调用浏览器"); }, properties,
                new MockEnvironment().withProperty("spring.profiles.active", "local"));

        assertTrue(guarded.publishHtml(7, "html-dev", html("green"), "legacy", "STOP").published());
    }

    @Test
    void productionProfileCannotDisableOptionalSmokeGate() {
        var fixture = fixture();
        var properties = new HtmlArtifactProperties();
        properties.setEnabled(false);
        properties.setRequired(false);
        MockEnvironment production = new MockEnvironment();
        production.setActiveProfiles("prod");
        var guarded = new ArtifactPublicationService(new MultiFileArtifactValidator(), new HtmlArtifactValidator(),
                fixture.resolver, fixture.mapper, null, path -> HtmlSmokeTestResult.success(), properties, production);

        var error = assertThrows(ArtifactValidationException.class,
                () -> guarded.publishHtml(7, "html-prod", html("green"), "legacy", "STOP"));
        assertEquals("HTML_SMOKE_TEST_UNAVAILABLE", error.getErrorCode());
    }

    @Test
    void conflictingHtmlRetryPreservesCurrentVersion() throws Exception {
        var fixture = fixture();
        fixture.service.publishHtml(7, "html-1", html("blue"), "legacy", "STOP");

        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishHtml(7, "html-1", html("red"), "legacy", "STOP"));

        assertEquals("ARTIFACT_VERSION_CONFLICT", error.getErrorCode());
        assertTrue(Files.readString(fixture.resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 7)
                .resolve("index.html")).contains("blue"));
    }

    @Test
    void invalidRequestIdIsRejectedBeforeHtmlParsing() {
        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture().service.publishHtml(7, "../bad", "not html", "legacy", "STOP"));
        assertEquals("REQUEST_ID_INVALID", error.getErrorCode());
    }

    @Test
    void htmlPointerReplacementFailurePreservesPreviousRelease() throws Exception {
        var fixture = fixture();
        fixture.service.publishHtml(7, "html-old", html("blue"), "legacy", "STOP");
        Path appRoot = root.resolve("html_7");
        Files.delete(appRoot.resolve(".current"));
        Files.createDirectory(appRoot.resolve(".current"));

        assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishHtml(7, "html-new", html("green"), "legacy", "STOP"));
        assertTrue(Files.isDirectory(appRoot.resolve(".current")));
        assertTrue(Files.isRegularFile(appRoot.resolve(".releases/html-old/index.html")));
        Path active = fixture.resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 7);
        assertTrue(active.endsWith("html-old"));
        assertTrue(Files.readString(active.resolve("index.html")).contains("blue"));
        assertFalse(Files.isRegularFile(appRoot.resolve(".committed/html-new")));
    }

    @Test
    void htmlTombstonePermanentlyPreventsCleanedRequestReplay() throws Exception {
        var fixture = fixture();
        for (int i = 1; i <= 5; i++) fixture.service.publishHtml(7, "html-" + i, html("c" + i), "legacy", "STOP");
        Path appRoot = root.resolve("html_7");
        assertFalse(Files.exists(appRoot.resolve(".releases/html-1")));
        assertTrue(Files.isRegularFile(appRoot.resolve(".published/html-1.json")));

        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishHtml(7, "html-1", html("c1"), "legacy", "STOP"));
        assertEquals("ARTIFACT_VERSION_CONFLICT", error.getErrorCode());
        assertEquals("html-5", Files.readString(appRoot.resolve(".current")).trim());
    }

    @Test
    void olderHtmlReleaseCannotReplaceNewerCurrentAndRetentionKeepsThree() throws Exception {
        var fixture = fixture();
        for (int i = 1; i <= 5; i++) fixture.service.publishHtml(7, "html-" + i, html("c" + i), "legacy", "STOP");
        Path appRoot = root.resolve("html_7");
        try (var releases = Files.list(appRoot.resolve(".releases"))) { assertEquals(3, releases.count()); }

        var error = assertThrows(ArtifactValidationException.class,
                () -> fixture.service.publishHtml(7, "html-3", html("c3"), "legacy", "STOP"));
        assertEquals("ARTIFACT_VERSION_CONFLICT", error.getErrorCode());
        assertEquals("html-5", Files.readString(appRoot.resolve(".current")).trim());
    }

    @Test
    void htmlFallsBackToLegacyFlatRootBeforeFirstRelease() throws Exception {
        Path legacy = root.resolve("html_7");
        Files.createDirectories(legacy);
        Files.writeString(legacy.resolve("index.html"), "legacy");
        assertEquals(legacy.toAbsolutePath(), fixture().resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 7));
    }

    @Test
    void temporaryTombstoneIsIgnoredWhenComputingNextSequence() throws Exception {
        var fixture = fixture();
        Path published = root.resolve("html_7/.published");
        Files.createDirectories(published);
        Files.writeString(published.resolve(".html-1.json.tmp"), "not-json");

        fixture.service.publishHtml(7, "html-1", html("blue"), "legacy", "STOP");

        var manifest = fixture.mapper.readValue(root.resolve("html_7/.releases/html-1/manifest.json").toFile(), ArtifactManifest.class);
        assertEquals(1L, manifest.sequence());
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

    private String html(String color) {
        return "```html\n<!doctype html><html><head><style>body { color: " + color
                + "; }</style></head><body>ok</body></html>\n```";
    }
    private record Fixture(ArtifactPathResolver resolver, ArtifactPublicationService service, ObjectMapper mapper) { }
}
