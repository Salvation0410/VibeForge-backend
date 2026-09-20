package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.HtmlArtifactProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.assertDoesNotThrow;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class HtmlOutputBudgetGuardTest {
    @TempDir
    Path outputRoot;

    @Test
    void allowsHtmlWithoutActiveSource() {
        assertDoesNotThrow(() -> guard(5).checkRewriteAllowed(CodeGenTypeEnum.HTML, 1L));
    }

    @Test
    void allowsHtmlBelowThreshold() throws Exception {
        writeLegacyHtml(1L, "1234");
        assertDoesNotThrow(() -> guard(5).checkRewriteAllowed(CodeGenTypeEnum.HTML, 1L));
    }

    @Test
    void allowsHtmlAtThreshold() throws Exception {
        writeLegacyHtml(1L, "12345");
        assertDoesNotThrow(() -> guard(5).checkRewriteAllowed(CodeGenTypeEnum.HTML, 1L));
    }

    @Test
    void rejectsHtmlAboveThreshold() throws Exception {
        writeLegacyHtml(1L, "123456");

        ArtifactValidationException error = assertThrows(ArtifactValidationException.class,
                () -> guard(5).checkRewriteAllowed(CodeGenTypeEnum.HTML, 1L));

        assertEquals("HTML_OUTPUT_BUDGET_EXCEEDED", error.getErrorCode());
        assertEquals("index.html", error.getFile());
    }

    @Test
    void allowsNonHtmlTypesRegardlessOfSourceSize() throws Exception {
        Path multiFile = outputRoot.resolve("multi_file_1/index.html");
        Files.createDirectories(multiFile.getParent());
        Files.writeString(multiFile, "123456");

        assertDoesNotThrow(() -> guard(5).checkRewriteAllowed(CodeGenTypeEnum.MULTI_FILE, 1L));
        assertDoesNotThrow(() -> guard(5).checkRewriteAllowed(CodeGenTypeEnum.VUE_PROJECT, 1L));
    }

    private HtmlOutputBudgetGuard guard(int limit) {
        HtmlArtifactProperties properties = new HtmlArtifactProperties();
        properties.setMaxRewriteSourceChars(limit);
        return new HtmlOutputBudgetGuard(new ArtifactPathResolver(outputRoot, new ObjectMapper()), properties);
    }

    private void writeLegacyHtml(long appId, String content) throws Exception {
        Path indexFile = outputRoot.resolve("html_" + appId).resolve("index.html");
        Files.createDirectories(indexFile.getParent());
        Files.writeString(indexFile, content);
    }
}
