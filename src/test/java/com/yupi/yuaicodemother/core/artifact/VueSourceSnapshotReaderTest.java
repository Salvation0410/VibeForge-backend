package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class VueSourceSnapshotReaderTest {

    @TempDir
    Path tempDir;

    @Test
    void returnsOnlyEligibleSourcesInStablePriorityOrder() throws Exception {
        write("package.json", "{}");
        write("src/main.ts", "main");
        write("src/App.vue", "app");
        write("src/components/ZCard.vue", "card");
        write("node_modules/pkg/index.js", "dependency");
        write("dist/app.js", "bundle");
        write("build/app.js", "build");
        write(".git/config", "git");
        write("src/.private/Secret.vue", "secret");
        write(".env", "secret");
        write("package-lock.json", "lock");
        write("pnpm-lock.yaml", "lock");
        write("yarn.lock", "lock");
        Files.write(tempDir.resolve("image.png"), new byte[]{0, 1, 2});
        Files.write(tempDir.resolve("src/binary.ts"), new byte[]{'a', 0, 'b'});
        createSymbolicLinkIfSupported(tempDir.resolve("src/Linked.vue"), tempDir.resolve("src/App.vue"));

        Map<String, Object> snapshot = reader(tempDir).read(42L);

        assertEquals(List.of("package.json", "src/main.ts", "src/App.vue", "src/components/ZCard.vue"),
                files(snapshot).stream().map(file -> file.get("path")).toList());
        assertEquals(4, snapshot.get("eligibleFileCount"));
        assertEquals(4, snapshot.get("includedFileCount"));
        assertEquals(0, snapshot.get("omittedFileCount"));
        assertEquals(false, snapshot.get("truncated"));
        files(snapshot).forEach(file -> assertEquals(Set.of("path", "content", "truncated"), file.keySet()));
    }

    @Test
    void limitsSnapshotToTwentyFourFiles() throws Exception {
        for (int index = 0; index < 25; index++) {
            write("src/file-%02d.ts".formatted(index), "export default " + index);
        }

        Map<String, Object> snapshot = reader(tempDir).read(42L);

        assertEquals(25, snapshot.get("eligibleFileCount"));
        assertEquals(24, snapshot.get("includedFileCount"));
        assertEquals(1, snapshot.get("omittedFileCount"));
        assertEquals(true, snapshot.get("truncated"));
        assertEquals(24, files(snapshot).size());
    }

    @Test
    void truncatesSingleFileWithinPerFileBudget() throws Exception {
        write("src/App.vue", "x".repeat(VueSourceSnapshotReader.MAX_FILE_CHARS + 1));

        Map<String, Object> snapshot = reader(tempDir).read(42L);
        Map<String, Object> file = files(snapshot).getFirst();
        String content = (String) file.get("content");

        assertTrue(content.length() <= VueSourceSnapshotReader.MAX_FILE_CHARS);
        assertTrue(content.contains(VueSourceSnapshotReader.TRUNCATION_MARKER));
        assertEquals(true, file.get("truncated"));
        assertEquals(true, snapshot.get("truncated"));
    }

    @Test
    void enforcesTotalContentBudgetAndReportsAccurateMetadata() throws Exception {
        for (int index = 0; index < 6; index++) {
            write("src/file-%02d.ts".formatted(index), String.valueOf(index).repeat(11_000));
        }

        Map<String, Object> snapshot = reader(tempDir).read(42L);
        List<Map<String, Object>> files = files(snapshot);
        int totalChars = files.stream().mapToInt(file -> ((String) file.get("content")).length()).sum();

        assertTrue(totalChars <= VueSourceSnapshotReader.MAX_TOTAL_CHARS);
        assertEquals(6, snapshot.get("eligibleFileCount"));
        assertEquals(files.size(), snapshot.get("includedFileCount"));
        assertEquals(6 - files.size(), snapshot.get("omittedFileCount"));
        assertEquals(true, snapshot.get("truncated"));
        assertTrue(files.stream().anyMatch(file -> Boolean.TRUE.equals(file.get("truncated")))
                || (int) snapshot.get("omittedFileCount") > 0);
    }

    @Test
    void includesSmallFileThatFitsWhenRemainingBudgetCannotHoldTruncationMarker() throws Exception {
        for (int index = 0; index < 4; index++) {
            write("src/file-%02d.ts".formatted(index), String.valueOf(index).repeat(12_000));
        }
        write("src/file-04.ts", "e".repeat(11_985));
        write("src/file-05.ts", "small-file");

        Map<String, Object> snapshot = reader(tempDir).read(42L);
        List<Map<String, Object>> files = files(snapshot);
        int totalChars = files.stream().mapToInt(file -> ((String) file.get("content")).length()).sum();

        assertEquals(6, snapshot.get("eligibleFileCount"));
        assertEquals(6, snapshot.get("includedFileCount"));
        assertEquals(0, snapshot.get("omittedFileCount"));
        assertEquals(false, snapshot.get("truncated"));
        assertEquals("small-file", files.getLast().get("content"));
        assertEquals(59_995, totalChars);
    }

    @Test
    void reportsMissingAndEmptyProjectsWithStableErrors() throws Exception {
        Path missing = tempDir.resolve("missing");
        BusinessException missingError = assertThrows(BusinessException.class, () -> reader(missing).read(42L));
        assertTrue(missingError.getMessage().startsWith("VUE_SOURCE_SNAPSHOT_MISSING"));

        Path empty = tempDir.resolve("empty");
        Files.createDirectories(empty);
        writeAt(empty, "image.png", "not source");
        BusinessException emptyError = assertThrows(BusinessException.class, () -> reader(empty).read(42L));
        assertTrue(emptyError.getMessage().startsWith("VUE_SOURCE_SNAPSHOT_EMPTY"));
    }

    @Test
    void failsWholeSnapshotOnMalformedUtf8InsteadOfReturningPartialContent() throws Exception {
        write("src/Good.vue", "<template />");
        Path malformed = tempDir.resolve("src/ZBroken.ts");
        Files.write(malformed, new byte[]{(byte) 0xC3, (byte) 0x28});

        BusinessException error = assertThrows(BusinessException.class, () -> reader(tempDir).read(42L));

        assertTrue(error.getMessage().startsWith("VUE_SOURCE_SNAPSHOT_READ_FAILED"));
    }

    @Test
    void resolvesTheAuthoritativeVueProjectRoot() throws Exception {
        write("src/App.vue", "<template />");
        ArtifactPathResolver resolver = mock(ArtifactPathResolver.class);
        when(resolver.resolveActiveRoot(CodeGenTypeEnum.VUE_PROJECT, 73L)).thenReturn(tempDir);

        Map<String, Object> snapshot = new VueSourceSnapshotReader(resolver).read(73L);

        assertFalse(files(snapshot).isEmpty());
        verify(resolver).resolveActiveRoot(CodeGenTypeEnum.VUE_PROJECT, 73L);
    }

    private VueSourceSnapshotReader reader(Path root) {
        ArtifactPathResolver resolver = mock(ArtifactPathResolver.class);
        when(resolver.resolveActiveRoot(CodeGenTypeEnum.VUE_PROJECT, 42L)).thenReturn(root);
        return new VueSourceSnapshotReader(resolver);
    }

    @SuppressWarnings("unchecked")
    private List<Map<String, Object>> files(Map<String, Object> snapshot) {
        return (List<Map<String, Object>>) snapshot.get("files");
    }

    private void write(String relativePath, String content) throws IOException {
        writeAt(tempDir, relativePath, content);
    }

    private void writeAt(Path root, String relativePath, String content) throws IOException {
        Path file = root.resolve(relativePath);
        Files.createDirectories(file.getParent());
        Files.writeString(file, content, StandardCharsets.UTF_8);
    }

    private void createSymbolicLinkIfSupported(Path link, Path target) {
        try {
            Files.createSymbolicLink(link, target);
            assertTrue(Files.isSymbolicLink(link));
        } catch (IOException | UnsupportedOperationException | SecurityException ignored) {
            // Windows 未启用开发者模式时通常无法创建符号链接，不影响其余过滤断言。
        }
    }
}
