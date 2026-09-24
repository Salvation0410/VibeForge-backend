package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.CharBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CoderResult;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.channels.SeekableByteChannel;
import java.nio.file.FileVisitOption;
import java.nio.file.FileVisitResult;
import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.OpenOption;
import java.nio.file.Path;
import java.nio.file.SimpleFileVisitor;
import java.nio.file.StandardOpenOption;
import java.nio.file.attribute.BasicFileAttributes;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.EnumSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/** 从 Spring 权威项目目录生成有界、稳定的 Vue 源码快照。 */
@Component
@RequiredArgsConstructor
public class VueSourceSnapshotReader {
    static final int MAX_FILES = 24;
    static final int MAX_FILE_CHARS = 12_000;
    static final int MAX_TOTAL_CHARS = 60_000;
    static final String TRUNCATION_MARKER = "\n...[truncated]...\n";
    /** 单文件最多读取 64 KiB；大文件各探测真实首尾 32 KiB。 */
    static final int MAX_FILE_PROBE_BYTES = 64 * 1024;

    private static final int PROBE_SIDE_BYTES = MAX_FILE_PROBE_BYTES / 2;

    private static final Set<String> EXCLUDED_DIRECTORIES = Set.of("node_modules", "dist", "build");
    private static final Set<String> EXCLUDED_FILES = Set.of("package-lock.json", "pnpm-lock.yaml", "yarn.lock");
    private static final Set<String> ALLOWED_EXTENSIONS =
            Set.of("vue", "ts", "tsx", "js", "jsx", "css", "scss", "less", "html", "json");

    private final ArtifactPathResolver artifactPathResolver;

    /** 读取指定应用当前活动 Vue 项目的源码快照。 */
    public Map<String, Object> read(long appId) {
        Path root = artifactPathResolver.resolveActiveRoot(CodeGenTypeEnum.VUE_PROJECT, appId);
        if (!Files.isDirectory(root, LinkOption.NOFOLLOW_LINKS) || Files.isSymbolicLink(root)) {
            throw failure("VUE_SOURCE_SNAPSHOT_MISSING", "active Vue project directory is missing");
        }

        List<Path> eligiblePaths = collectEligiblePaths(root);
        if (eligiblePaths.isEmpty()) {
            throw failure("VUE_SOURCE_SNAPSHOT_EMPTY", "active Vue project contains no eligible source files");
        }

        // 先校验全部合格文件，再组装有界结果，避免后排读取失败时泄露部分成功。
        List<SourceFile> sources = new ArrayList<>();
        for (Path path : eligiblePaths) {
            SourceFile source = readSource(root, path);
            if (source != null) sources.add(source);
        }
        if (sources.isEmpty()) {
            throw failure("VUE_SOURCE_SNAPSHOT_EMPTY", "active Vue project contains no eligible text source files");
        }
        sources.sort(Comparator.comparingInt((SourceFile source) -> priority(source.path()))
                .thenComparing(SourceFile::path));
        return buildSnapshot(sources);
    }

    private List<Path> collectEligiblePaths(Path root) {
        List<Path> paths = new ArrayList<>();
        try {
            Files.walkFileTree(root, EnumSet.noneOf(FileVisitOption.class), Integer.MAX_VALUE,
                    new SimpleFileVisitor<>() {
                        @Override
                        public FileVisitResult preVisitDirectory(Path directory, BasicFileAttributes attributes) {
                            if (!directory.equals(root) && isExcludedDirectory(directory.getFileName().toString())) {
                                return FileVisitResult.SKIP_SUBTREE;
                            }
                            return FileVisitResult.CONTINUE;
                        }

                        @Override
                        public FileVisitResult visitFile(Path file, BasicFileAttributes attributes) {
                            if (attributes.isRegularFile() && !attributes.isSymbolicLink() && isEligibleFile(file)) {
                                paths.add(file);
                            }
                            return FileVisitResult.CONTINUE;
                        }

                        @Override
                        public FileVisitResult visitFileFailed(Path file, IOException exception) throws IOException {
                            throw exception;
                        }

                        @Override
                        public FileVisitResult postVisitDirectory(Path directory, IOException exception) throws IOException {
                            if (exception != null) throw exception;
                            return FileVisitResult.CONTINUE;
                        }
                    });
            return paths;
        } catch (Exception exception) {
            throw readFailure(root);
        }
    }

    private SourceFile readSource(Path root, Path file) {
        Set<OpenOption> options = Set.of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS);
        try (SeekableByteChannel channel = Files.newByteChannel(file, options)) {
            long size = channel.size();
            String path = portablePath(root.relativize(file));
            if (size <= MAX_FILE_PROBE_BYTES) {
                byte[] bytes = readBytes(channel, 0, Math.toIntExact(size));
                if (containsNulByte(bytes)) return null;
                String content = decodeStrictUtf8(bytes);
                boolean truncated = content.length() > MAX_FILE_CHARS;
                return new SourceFile(path, truncated ? truncate(content, MAX_FILE_CHARS) : content, truncated);
            }

            byte[] headBytes = readBytes(channel, 0, PROBE_SIDE_BYTES);
            byte[] tailBytes = readBytes(channel, size - PROBE_SIDE_BYTES, PROBE_SIDE_BYTES);
            if (containsNulByte(headBytes) || containsNulByte(tailBytes)) return null;
            String head = decodeHeadProbe(headBytes);
            String tail = decodeTailProbe(tailBytes);
            return new SourceFile(path, truncate(head, tail, MAX_FILE_CHARS), true);
        } catch (Exception exception) {
            throw readFailure(file);
        }
    }

    private byte[] readBytes(SeekableByteChannel channel, long position, int length) throws IOException {
        channel.position(position);
        ByteBuffer buffer = ByteBuffer.allocate(length);
        while (buffer.hasRemaining()) {
            if (channel.read(buffer) < 0) throw new IOException("unexpected end of file");
        }
        return buffer.array();
    }

    private Map<String, Object> buildSnapshot(List<SourceFile> sources) {
        List<Map<String, Object>> files = new ArrayList<>();
        int totalChars = 0;
        boolean truncated = sources.size() > MAX_FILES;
        for (SourceFile source : sources) {
            if (files.size() >= MAX_FILES) break;
            int remaining = MAX_TOTAL_CHARS - totalChars;
            boolean exceedsRemainingBudget = source.content().length() > remaining;
            // 完整内容可放入时不需要为截断标记预留预算。
            if (exceedsRemainingBudget && remaining <= TRUNCATION_MARKER.length()) {
                truncated = true;
                break;
            }
            boolean fileTruncated = source.truncated() || exceedsRemainingBudget;
            String content = exceedsRemainingBudget
                    ? truncate(source.content(), remaining)
                    : source.content();
            Map<String, Object> file = new LinkedHashMap<>();
            file.put("path", source.path());
            file.put("content", content);
            file.put("truncated", fileTruncated);
            files.add(Map.copyOf(file));
            totalChars += content.length();
            truncated |= fileTruncated;
        }

        int included = files.size();
        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put("files", List.copyOf(files));
        snapshot.put("eligibleFileCount", sources.size());
        snapshot.put("includedFileCount", included);
        snapshot.put("omittedFileCount", sources.size() - included);
        snapshot.put("truncated", truncated || included < sources.size());
        return Map.copyOf(snapshot);
    }

    private boolean isExcludedDirectory(String name) {
        return name.startsWith(".") || EXCLUDED_DIRECTORIES.contains(name);
    }

    private boolean isEligibleFile(Path file) {
        String name = file.getFileName().toString();
        if (name.startsWith(".") || EXCLUDED_FILES.contains(name)) return false;
        int separator = name.lastIndexOf('.');
        return separator >= 0 && separator < name.length() - 1
                && ALLOWED_EXTENSIONS.contains(name.substring(separator + 1).toLowerCase(Locale.ROOT));
    }

    private boolean containsNulByte(byte[] bytes) {
        for (byte value : bytes) {
            if (value == 0) return true;
        }
        return false;
    }

    private String decodeStrictUtf8(byte[] bytes) throws CharacterCodingException {
        CharBuffer decoded = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(bytes));
        return decoded.toString();
    }

    /** 首部探测只允许忽略末端最多三个 UTF-8 边界字节。 */
    private String decodeHeadProbe(byte[] bytes) throws CharacterCodingException {
        var decoder = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT);
        ByteBuffer input = ByteBuffer.wrap(bytes);
        CharBuffer output = CharBuffer.allocate(bytes.length);
        CoderResult result = decoder.decode(input, output, false);
        if (result.isError()) result.throwException();
        if (input.remaining() > 3) throw new CharacterCodingException();
        output.flip();
        return output.toString();
    }

    /** 尾部探测只允许忽略开端最多三个 UTF-8 边界字节。 */
    private String decodeTailProbe(byte[] bytes) throws CharacterCodingException {
        int start = 0;
        while (start < bytes.length && start < 3 && isUtf8Continuation(bytes[start])) start++;
        return decodeStrictUtf8(java.util.Arrays.copyOfRange(bytes, start, bytes.length));
    }

    private boolean isUtf8Continuation(byte value) {
        return (value & 0xC0) == 0x80;
    }

    /** 截断标记计入额度，并尽量均衡保留文件头尾。 */
    private String truncate(String content, int limit) {
        if (content.length() <= limit) return content;
        int retained = limit - TRUNCATION_MARKER.length();
        int head = (retained + 1) / 2;
        int tail = retained - head;
        return safePrefix(content, head) + TRUNCATION_MARKER + safeSuffix(content, tail);
    }

    private String truncate(String headContent, String tailContent, int limit) {
        int retained = limit - TRUNCATION_MARKER.length();
        int head = (retained + 1) / 2;
        int tail = retained - head;
        return safePrefix(headContent, head) + TRUNCATION_MARKER + safeSuffix(tailContent, tail);
    }

    private String safePrefix(String content, int limit) {
        int end = Math.min(content.length(), limit);
        if (end > 0 && end < content.length()
                && Character.isHighSurrogate(content.charAt(end - 1))
                && Character.isLowSurrogate(content.charAt(end))) {
            end--;
        }
        return content.substring(0, end);
    }

    private String safeSuffix(String content, int limit) {
        int start = Math.max(0, content.length() - limit);
        if (start > 0 && start < content.length()
                && Character.isHighSurrogate(content.charAt(start - 1))
                && Character.isLowSurrogate(content.charAt(start))) {
            start++;
        }
        return content.substring(start);
    }

    private int priority(String path) {
        if ("package.json".equals(path)) return 0;
        if (path.matches("src/main\\.(?:ts|tsx|js|jsx)")) return 1;
        if ("src/App.vue".equals(path)) return 2;
        return 3;
    }

    private String portablePath(Path path) {
        return path.toString().replace('\\', '/');
    }

    private BusinessException readFailure(Path target) {
        return failure("VUE_SOURCE_SNAPSHOT_READ_FAILED", "failed to read " + target);
    }

    private BusinessException failure(String prefix, String detail) {
        return new BusinessException(ErrorCode.OPERATION_ERROR, prefix + ": " + detail);
    }

    private record SourceFile(String path, String content, boolean truncated) {
    }
}
