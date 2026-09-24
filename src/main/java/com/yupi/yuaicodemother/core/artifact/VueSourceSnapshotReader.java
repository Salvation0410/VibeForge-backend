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
import java.util.NavigableSet;
import java.util.Set;
import java.util.TreeSet;

/** 从 Spring 权威项目目录生成有界、稳定的 Vue 源码快照。 */
@Component
@RequiredArgsConstructor
public class VueSourceSnapshotReader {
    static final int MAX_FILES = 24;
    static final int MAX_FILE_CHARS = 12_000;
    static final int MAX_TOTAL_CHARS = 60_000;
    static final int MAX_ELIGIBLE_FILES = 10_000;
    static final int MAX_VISITED_ENTRIES = 20_000;
    static final int MAX_SOURCE_FILE_BYTES = 1024 * 1024;
    static final String TRUNCATION_MARKER = "\n...[truncated]...\n";

    private static final int READ_BUFFER_BYTES = 8 * 1024;
    private static final int RETAINED_HEAD_CHARS =
            (MAX_FILE_CHARS - TRUNCATION_MARKER.length() + 1) / 2;
    private static final int RETAINED_TAIL_CHARS =
            MAX_FILE_CHARS - TRUNCATION_MARKER.length() - RETAINED_HEAD_CHARS;

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

        CandidateSelection selection = collectEligiblePaths(root, new EntryBudget());
        if (selection.eligibleFileCount() == 0) {
            throw failure("VUE_SOURCE_SNAPSHOT_EMPTY", "active Vue project contains no eligible source files");
        }

        List<SourceFile> sources = new ArrayList<>();
        for (Path relativePath : selection.paths()) {
            sources.add(readSource(root, root.resolve(relativePath)));
        }
        return buildSnapshot(sources, selection.eligibleFileCount());
    }

    CandidateSelection collectEligiblePaths(Path root, EntryBudget entryBudget) {
        CandidateCollector collector = new CandidateCollector();
        try {
            Files.walkFileTree(root, EnumSet.noneOf(FileVisitOption.class), Integer.MAX_VALUE,
                    new SimpleFileVisitor<>() {
                        @Override
                        public FileVisitResult preVisitDirectory(Path directory, BasicFileAttributes attributes) {
                            if (!directory.equals(root)) entryBudget.consume();
                            if (!directory.equals(root) && isExcludedDirectory(directory.getFileName().toString())) {
                                return FileVisitResult.SKIP_SUBTREE;
                            }
                            return FileVisitResult.CONTINUE;
                        }

                        @Override
                        public FileVisitResult visitFile(Path file, BasicFileAttributes attributes) {
                            entryBudget.consume();
                            if (attributes.isRegularFile() && !attributes.isSymbolicLink() && isEligibleFile(file)) {
                                collector.add(root.relativize(file));
                            }
                            return FileVisitResult.CONTINUE;
                        }

                        @Override
                        public FileVisitResult visitFileFailed(Path file, IOException exception) throws IOException {
                            entryBudget.consume();
                            throw exception;
                        }

                        @Override
                        public FileVisitResult postVisitDirectory(Path directory, IOException exception) throws IOException {
                            if (exception != null) throw exception;
                            return FileVisitResult.CONTINUE;
                        }
                    });
            return collector.selection();
        } catch (BusinessException exception) {
            throw exception;
        } catch (Exception exception) {
            throw readFailure();
        }
    }

    private SourceFile readSource(Path root, Path file) {
        Set<OpenOption> options = Set.of(StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS);
        try (SeekableByteChannel channel = Files.newByteChannel(file, options)) {
            long size = channel.size();
            if (size > MAX_SOURCE_FILE_BYTES) {
                throw failure("VUE_SOURCE_SNAPSHOT_READ_FAILED", "source file too large");
            }
            RetainedText retained = scanUtf8(channel);
            return new SourceFile(portablePath(root.relativize(file)), retained.content(), retained.truncated());
        } catch (BusinessException exception) {
            throw exception;
        } catch (Exception exception) {
            throw readFailure();
        }
    }

    /** 完整扫描 UTF-8，仅保留生成快照所需的固定首尾字符窗口。 */
    private RetainedText scanUtf8(SeekableByteChannel channel) throws Exception {
        var decoder = StandardCharsets.UTF_8.newDecoder()
                .onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT);
        ByteBuffer input = ByteBuffer.allocate(READ_BUFFER_BYTES);
        CharBuffer output = CharBuffer.allocate(READ_BUFFER_BYTES);
        TextAccumulator accumulator = new TextAccumulator();
        boolean endOfInput = false;
        long bytesRead = 0;
        while (!endOfInput) {
            int read = channel.read(input);
            endOfInput = read < 0;
            if (read > 0 && (bytesRead += read) > MAX_SOURCE_FILE_BYTES) {
                throw failure("VUE_SOURCE_SNAPSHOT_READ_FAILED", "source file too large");
            }
            input.flip();
            for (int index = input.position(); index < input.limit(); index++) {
                if (input.get(index) == 0) throw new CharacterCodingException();
            }
            while (true) {
                CoderResult result = decoder.decode(input, output, endOfInput);
                drain(output, accumulator);
                if (result.isError()) result.throwException();
                if (!result.isOverflow()) break;
            }
            input.compact();
        }
        while (true) {
            CoderResult result = decoder.flush(output);
            drain(output, accumulator);
            if (result.isError()) result.throwException();
            if (!result.isOverflow()) break;
        }
        return accumulator.finish();
    }

    private void drain(CharBuffer output, TextAccumulator accumulator) {
        output.flip();
        accumulator.append(output);
        output.clear();
    }

    private Map<String, Object> buildSnapshot(List<SourceFile> sources, int eligibleFileCount) {
        List<Map<String, Object>> files = new ArrayList<>();
        int totalChars = 0;
        boolean truncated = eligibleFileCount > sources.size();
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
        snapshot.put("eligibleFileCount", eligibleFileCount);
        snapshot.put("includedFileCount", included);
        snapshot.put("omittedFileCount", eligibleFileCount - included);
        snapshot.put("truncated", truncated || included < eligibleFileCount);
        return Map.copyOf(snapshot);
    }

    private boolean isExcludedDirectory(String name) {
        return name.startsWith(".") || EXCLUDED_DIRECTORIES.contains(name.toLowerCase(Locale.ROOT));
    }

    private boolean isEligibleFile(Path file) {
        String name = file.getFileName().toString();
        String normalizedName = name.toLowerCase(Locale.ROOT);
        if (name.startsWith(".") || EXCLUDED_FILES.contains(normalizedName)) return false;
        int separator = name.lastIndexOf('.');
        return separator >= 0 && separator < name.length() - 1
                && ALLOWED_EXTENSIONS.contains(normalizedName.substring(separator + 1));
    }

    /** 截断标记计入额度，并尽量均衡保留文件头尾。 */
    private String truncate(String content, int limit) {
        if (content.length() <= limit) return content;
        int retained = limit - TRUNCATION_MARKER.length();
        int head = (retained + 1) / 2;
        int tail = retained - head;
        return safePrefix(content, head) + TRUNCATION_MARKER + safeSuffix(content, tail);
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

    private static int priority(String path) {
        if ("package.json".equals(path)) return 0;
        if (path.matches("src/main\\.(?:ts|tsx|js|jsx)")) return 1;
        if ("src/App.vue".equals(path)) return 2;
        return 3;
    }

    private String portablePath(Path path) {
        return path.toString().replace('\\', '/');
    }

    private BusinessException readFailure() {
        return failure("VUE_SOURCE_SNAPSHOT_READ_FAILED", "failed to read project source");
    }

    private BusinessException failure(String prefix, String detail) {
        return new BusinessException(ErrorCode.OPERATION_ERROR, prefix + ": " + detail);
    }

    private record SourceFile(String path, String content, boolean truncated) {
    }

    private record CandidateSelection(List<Path> paths, int eligibleFileCount) {
    }

    private record RetainedText(String content, boolean truncated) {
    }

    /** 对根目录以外的所有遍历条目施加统一硬上限。 */
    static final class EntryBudget {
        private final int limit;
        private int visitedEntries;

        EntryBudget() {
            this(MAX_VISITED_ENTRIES);
        }

        EntryBudget(int limit) {
            if (limit <= 0) throw new IllegalArgumentException("limit must be positive");
            this.limit = limit;
        }

        void consume() {
            if (++visitedEntries > limit) {
                throw failureStatic("VUE_SOURCE_SNAPSHOT_READ_FAILED", "too many project entries");
            }
        }
    }

    /** 遍历期间只保存排序最佳的 24 个候选，并硬限制合格文件总数。 */
    static final class CandidateCollector {
        private static final Comparator<Path> PATH_COMPARATOR = Comparator
                .comparingInt((Path path) -> priority(portable(path)))
                .thenComparing(CandidateCollector::portable);

        private final NavigableSet<Path> paths = new TreeSet<>(PATH_COMPARATOR);
        private int eligibleFileCount;

        void add(Path relativePath) {
            eligibleFileCount++;
            if (eligibleFileCount > MAX_ELIGIBLE_FILES) {
                throw failureStatic("VUE_SOURCE_SNAPSHOT_READ_FAILED", "too many eligible files");
            }
            paths.add(relativePath);
            if (paths.size() > MAX_FILES) paths.pollLast();
        }

        CandidateSelection selection() {
            return new CandidateSelection(List.copyOf(paths), eligibleFileCount);
        }

        List<Path> selectedPaths() {
            return List.copyOf(paths);
        }

        private static String portable(Path path) {
            return path.toString().replace('\\', '/');
        }
    }

    /** 固定保留完整短文本，或长文本所需的首尾字符。 */
    private static final class TextAccumulator {
        private final StringBuilder prefix = new StringBuilder(MAX_FILE_CHARS + 1);
        private final CharRingBuffer tail = new CharRingBuffer(RETAINED_TAIL_CHARS + 2);
        private long charCount;

        void append(CharBuffer characters) {
            while (characters.hasRemaining()) {
                char value = characters.get();
                charCount++;
                if (prefix.length() < MAX_FILE_CHARS + 2) prefix.append(value);
                tail.append(value);
            }
        }

        RetainedText finish() {
            if (charCount <= MAX_FILE_CHARS) return new RetainedText(prefix.toString(), false);
            String content = safePrefixStatic(prefix.toString(), RETAINED_HEAD_CHARS)
                    + TRUNCATION_MARKER + safeSuffixStatic(tail.content(), RETAINED_TAIL_CHARS);
            return new RetainedText(content, true);
        }
    }

    /** 固定容量字符环形缓冲，追加 O(1)，仅输出时线性展开。 */
    static final class CharRingBuffer {
        private final char[] values;
        private int start;
        private int size;

        CharRingBuffer(int capacity) {
            if (capacity <= 0) throw new IllegalArgumentException("capacity must be positive");
            values = new char[capacity];
        }

        void append(char value) {
            if (size < values.length) {
                values[(start + size) % values.length] = value;
                size++;
                return;
            }
            values[start] = value;
            start = (start + 1) % values.length;
        }

        String content() {
            char[] ordered = new char[size];
            int firstLength = Math.min(size, values.length - start);
            System.arraycopy(values, start, ordered, 0, firstLength);
            if (firstLength < size) {
                System.arraycopy(values, 0, ordered, firstLength, size - firstLength);
            }
            return new String(ordered);
        }
    }

    private static String safePrefixStatic(String content, int limit) {
        int end = Math.min(content.length(), limit);
        if (end > 0 && end < content.length() && Character.isHighSurrogate(content.charAt(end - 1))
                && Character.isLowSurrogate(content.charAt(end))) end--;
        return content.substring(0, end);
    }

    private static String safeSuffixStatic(String content, int limit) {
        int start = Math.max(0, content.length() - limit);
        if (start > 0 && start < content.length() && Character.isHighSurrogate(content.charAt(start - 1))
                && Character.isLowSurrogate(content.charAt(start))) start++;
        return content.substring(start);
    }

    private static BusinessException failureStatic(String prefix, String detail) {
        return new BusinessException(ErrorCode.OPERATION_ERROR, prefix + ": " + detail);
    }
}
