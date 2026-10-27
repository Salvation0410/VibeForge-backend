package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class ArtifactPublicationEvidenceTest {
    @TempDir Path root;

    @Test
    void committedEvidenceSurvivesReleaseRetentionWithoutChangingCurrentPointer() throws Exception {
        var mapper = new ObjectMapper().registerModule(new JavaTimeModule());
        var resolver = new ArtifactPathResolver(root, mapper);
        var store = new VersionedArtifactStore(resolver, mapper);
        for (int i = 1; i <= 4; i++) {
            store.publish(CodeGenTypeEnum.HTML, 42, "req-" + i, Map.of("index.html", "page-" + i), "test", "STOP", null);
        }
        Path app = root.resolve("html_42");
        assertFalse(Files.exists(app.resolve(".releases/req-1")));
        assertEquals("COMMITTED", resolver.publicationState(CodeGenTypeEnum.HTML, 42, "req-1"));
        assertEquals("COMMITTED", resolver.publicationState(CodeGenTypeEnum.HTML, 42, "req-4"));
        assertEquals("req-4", Files.readString(app.resolve(".current")));
        assertEquals("NOT_CONFIRMED", resolver.publicationState(CodeGenTypeEnum.HTML, 43, "req-4"));
        assertEquals("NOT_VERSIONED", resolver.publicationState(CodeGenTypeEnum.VUE_PROJECT, 42, "req-4"));
        assertEquals("UNAVAILABLE", resolver.publicationState(CodeGenTypeEnum.HTML, 42, "../bad"));
    }

    @Test
    void tombstoneAloneIsNotCommitAndCorruptCommittedReleaseIsUnavailable() throws Exception {
        var mapper = new ObjectMapper().registerModule(new JavaTimeModule());
        var resolver = new ArtifactPathResolver(root, mapper);
        var store = new VersionedArtifactStore(resolver, mapper);
        store.publish(CodeGenTypeEnum.MULTI_FILE, 42, "req", Map.of(
                "index.html", "page", "style.css", "style", "script.js", "script"), "test", "STOP", null);
        Path app = root.resolve("multi_file_42");
        assertEquals("COMMITTED", resolver.publicationState(CodeGenTypeEnum.MULTI_FILE, 42, "req"));
        Files.delete(app.resolve(".committed/req"));
        assertEquals("NOT_CONFIRMED", resolver.publicationState(CodeGenTypeEnum.MULTI_FILE, 42, "req"));
        Files.writeString(app.resolve(".committed/req"), "req");
        Files.writeString(app.resolve(".releases/req/style.css"), "tampered");
        assertEquals("UNAVAILABLE", resolver.publicationState(CodeGenTypeEnum.MULTI_FILE, 42, "req"));
        Files.writeString(app.resolve(".published/req.json"), "broken");
        assertEquals("UNAVAILABLE", resolver.publicationState(CodeGenTypeEnum.MULTI_FILE, 42, "req"));
    }
}
