package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;
import com.yupi.yuaicodemother.core.paser.MultiFileCodeParser;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;

/** 将通过硬校验的三文件产物发布为不可变版本。 */
@Service
public class ArtifactPublicationService {
    private final MultiFileCodeParser parser = new MultiFileCodeParser();
    private final MultiFileArtifactValidator validator;
    private final ArtifactPathResolver resolver;
    private final ObjectMapper objectMapper;

    /** 组装解析、校验、路径解析和 JSON manifest 能力。 */
    @Autowired
    public ArtifactPublicationService(MultiFileArtifactValidator validator, ArtifactPathResolver resolver, ObjectMapper objectMapper) {
        this.validator = validator; this.resolver = resolver; this.objectMapper = objectMapper;
    }

    /**
     * 严格解析并发布一个多文件版本；发布失败时 `.current` 保持指向上一成功版本。
     *
     * @param appId 应用 ID
     * @param requestId 同时作为幂等键的请求 ID
     * @param rawArtifact 模型返回的完整三文件文本
     * @param engine 生成引擎名称
     * @param finishReason 模型结束原因
     * @return 已发布版本及三个文件摘要
     */
    public synchronized ArtifactPublishResult publishMultiFile(long appId, String requestId, String rawArtifact,
                                                                String engine, String finishReason) {
        validateRequestId(requestId);
        MultiFileCodeResult artifact = parser.parseCode(rawArtifact);
        validator.validateOrThrow(artifact);
        Path root = resolver.projectRoot(CodeGenTypeEnum.MULTI_FILE, appId);
        Path releases = root.resolve(".releases");
        Path release = releases.resolve(requestId);
        Path tombstone = tombstonePath(root, requestId);
        Map<String, String> hashes = hashes(artifact);
        try {
            Files.createDirectories(releases);
            if (Files.isRegularFile(tombstone) && !Files.exists(release)) {
                throw versionConflict("该请求已发布但产物版本已被保留策略清理，禁止重建或回滚");
            }
            if (Files.exists(release)) return existingResult(release, requestId, hashes);
            long sequence = nextPublicationSequence(root);
            Path staging = root.resolve(".staging").resolve(requestId);
            deleteTree(staging);
            Files.createDirectories(staging);
            write(staging.resolve("index.html"), artifact.getHtmlCode());
            write(staging.resolve("style.css"), artifact.getCssCode());
            write(staging.resolve("script.js"), artifact.getJsCode());
            ArtifactManifest manifest = new ArtifactManifest(requestId, appId, safe(engine), Instant.now(),
                    sequence, hashes, safe(finishReason), 1);
            objectMapper.writeValue(staging.resolve("manifest.json").toFile(), manifest);
            verifyStaging(staging, hashes);
            Files.move(staging, release, StandardCopyOption.ATOMIC_MOVE);
            persistTombstone(root, manifest);
            switchPointer(root, requestId);
            try { cleanupOldReleases(root, requestId); }
            catch (Exception ignored) { /* 清理失败不改变已经原子提交的成功结果。 */ }
            return new ArtifactPublishResult(true, requestId, hashes);
        } catch (ArtifactValidationException e) { throw e; }
        catch (Exception e) {
            throw new ArtifactValidationException("ARTIFACT_PUBLISH_FAILED", null, "产物发布失败: " + e.getMessage());
        }
    }

    /**
     * 从磁盘重算已有版本摘要并校验 manifest；仅当目标发布序号更大时恢复未完成的指针切换。
     * <p>
     * 任一业务文件缺失、被篡改或摘要不符都会以版本冲突拒绝，绝不自动激活可疑版本。
     */
    private ArtifactPublishResult existingResult(Path release, String requestId, Map<String, String> hashes) throws Exception {
        ArtifactManifest manifest;
        Map<String, String> diskHashes;
        try {
            manifest = objectMapper.readValue(release.resolve("manifest.json").toFile(), ArtifactManifest.class);
            diskHashes = hashesFromRelease(release);
        } catch (Exception e) {
            throw versionConflict("相同请求 ID 的已有版本不完整或无法读取");
        }
        if (!requestId.equals(manifest.requestId()) || !hashes.equals(manifest.hashes()) || !hashes.equals(diskHashes)) {
            throw versionConflict("相同请求 ID 对应不同产物或磁盘版本已被篡改");
        }
        Path root = release.getParent().getParent();
        validateTombstoneIfPresent(root, manifest);
        Path pointer = root.resolve(".current");
        if (!Files.isRegularFile(pointer)) {
            ensureRecoverableSequence(manifest);
            persistTombstone(root, manifest);
            switchPointer(root, requestId);
        } else if (!requestId.equals(Files.readString(pointer, StandardCharsets.UTF_8).trim())) {
            ensureTargetSequenceIsNewer(root, manifest);
            persistTombstone(root, manifest);
            switchPointer(root, requestId);
        } else {
            persistTombstone(root, manifest);
        }
        return new ArtifactPublishResult(true, requestId, hashes);
    }

    /**
     * 读取已有 release 的三个业务文件并计算摘要；文件缺失或读取失败时直接失败。
     */
    private Map<String, String> hashesFromRelease(Path release) throws Exception {
        Map<String, String> values = new LinkedHashMap<>();
        for (String fileName : List.of("index.html", "style.css", "script.js")) {
            Path file = release.resolve(fileName);
            if (!Files.isRegularFile(file)) throw new NoSuchFileException(file.toString());
            values.put(fileName, sha256(Files.readString(file, StandardCharsets.UTF_8)));
        }
        return Map.copyOf(values);
    }

    /**
     * 确认目标版本的持久序号严格大于当前版本，防止时钟回拨或旧请求重放覆盖新版本。
     * 任一 manifest 缺少有效序号或无法读取时均以版本冲突拒绝恢复，并保留原指针。
     */
    private void ensureTargetSequenceIsNewer(Path root, ArtifactManifest targetManifest) {
        try {
            String currentId = Files.readString(root.resolve(".current"), StandardCharsets.UTF_8).trim();
            ArtifactManifest currentManifest = objectMapper.readValue(
                    root.resolve(".releases").resolve(currentId).resolve("manifest.json").toFile(),
                    ArtifactManifest.class);
            if (currentManifest.sequence() <= 0 || targetManifest.sequence() <= currentManifest.sequence()) {
                throw versionConflict("该请求版本早于或等于当前活动版本");
            }
        } catch (ArtifactValidationException e) {
            throw e;
        } catch (Exception e) {
            throw versionConflict("无法确认当前活动版本早于待恢复版本");
        }
    }

    /** 当前指针缺失时，仅允许有明确正序号的新格式 release 恢复，老 manifest 保守拒绝。 */
    private void ensureRecoverableSequence(ArtifactManifest manifest) {
        if (manifest.sequence() <= 0) {
            throw versionConflict("已有版本缺少有效发布序号，无法证明其恢复顺序");
        }
    }

    /**
     * 原子递增应用级发布序号；序号文件损坏时失败，避免重置后破坏单调顺序。
     * 调用方已通过应用级租约串行，同一服务实例内另由 publishMultiFile 的 synchronized 保护。
     */
    private long nextPublicationSequence(Path root) throws Exception {
        Path sequenceFile = root.resolve(".publication-sequence");
        long persisted = 0;
        if (Files.exists(sequenceFile)) {
            try {
                persisted = Long.parseLong(Files.readString(sequenceFile, StandardCharsets.UTF_8).trim());
                if (persisted < 0) throw new NumberFormatException("发布序号不能为负数");
            } catch (Exception e) {
                throw new IllegalStateException("发布序号文件损坏", e);
            }
        }
        long next = Math.addExact(Math.max(persisted, highestKnownSequence(root)), 1);
        writeStringAtomically(sequenceFile, Long.toString(next), ".publication-sequence.tmp");
        return next;
    }

    /** 扫描 release 与 tombstone 的最大已知序号，用于迁移或序号文件意外缺失后的单调续写。 */
    private long highestKnownSequence(Path root) throws Exception {
        long highest = 0;
        Path releases = root.resolve(".releases");
        if (Files.isDirectory(releases)) {
            try (var stream = Files.list(releases)) {
                for (Path release : stream.filter(Files::isDirectory).toList()) {
                    ArtifactManifest manifest = objectMapper.readValue(
                            release.resolve("manifest.json").toFile(), ArtifactManifest.class);
                    highest = Math.max(highest, manifest.sequence());
                }
            }
        }
        Path published = root.resolve(".published");
        if (Files.isDirectory(published)) {
            try (var stream = Files.list(published)) {
                for (Path marker : stream.filter(Files::isRegularFile)
                        .filter(path -> path.getFileName().toString().endsWith(".json")).toList()) {
                    PublishedRequest tombstone = objectMapper.readValue(marker.toFile(), PublishedRequest.class);
                    highest = Math.max(highest, tombstone.sequence());
                }
            }
        }
        return highest;
    }

    /**
     * 为成功落盘的 release 写持久发布墓碑；已有墓碑必须与 manifest 的序号和摘要完全一致。
     * 墓碑永不随 release 保留清理删除，用于永久阻止旧 requestId 被重建。
     */
    private void persistTombstone(Path root, ArtifactManifest manifest) throws Exception {
        Path tombstone = tombstonePath(root, manifest.requestId());
        PublishedRequest expected = new PublishedRequest(manifest.requestId(), manifest.sequence(), manifest.hashes());
        if (Files.exists(tombstone)) {
            PublishedRequest actual = objectMapper.readValue(tombstone.toFile(), PublishedRequest.class);
            if (!expected.equals(actual)) throw versionConflict("发布墓碑与已有版本不一致");
            return;
        }
        Files.createDirectories(tombstone.getParent());
        Path temporary = tombstone.resolveSibling("." + tombstone.getFileName() + ".tmp");
        Files.deleteIfExists(temporary);
        try {
            objectMapper.writeValue(temporary.toFile(), expected);
            Files.move(temporary, tombstone, StandardCopyOption.ATOMIC_MOVE);
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    /** 已有墓碑存在时校验其发布序号和摘要，任何不一致都视为不可恢复的版本冲突。 */
    private void validateTombstoneIfPresent(Path root, ArtifactManifest manifest) throws Exception {
        Path tombstone = tombstonePath(root, manifest.requestId());
        if (!Files.exists(tombstone)) return;
        PublishedRequest actual;
        try {
            actual = objectMapper.readValue(tombstone.toFile(), PublishedRequest.class);
        } catch (Exception e) {
            throw versionConflict("发布墓碑损坏或无法读取");
        }
        PublishedRequest expected = new PublishedRequest(manifest.requestId(), manifest.sequence(), manifest.hashes());
        if (!expected.equals(actual)) throw versionConflict("发布墓碑与已有版本不一致");
    }

    /** 返回 requestId 对应的永久发布墓碑路径；requestId 已在入口完成安全字符校验。 */
    private Path tombstonePath(Path root, String requestId) {
        return root.resolve(".published").resolve(requestId + ".json");
    }

    /** 创建统一的已有版本冲突异常，调用方不得在此类失败后切换活动指针。 */
    private ArtifactValidationException versionConflict(String message) {
        return new ArtifactValidationException("ARTIFACT_VERSION_CONFLICT", null, message);
    }

    /** 使用同目录临时文件原子替换当前指针，避免读到半写入内容。 */
    private void switchPointer(Path root, String requestId) throws Exception {
        Path temporary = root.resolve(".current.tmp-" + requestId);
        writeStringAtomically(root.resolve(".current"), requestId, temporary.getFileName().toString());
    }

    /** 仅按持久发布序号清理非当前旧版本，保留当前及最近两个版本，且永不删除墓碑。 */
    private void cleanupOldReleases(Path root, String current) throws Exception {
        try (var stream = Files.list(root.resolve(".releases"))) {
            List<Path> versions = stream.filter(Files::isDirectory)
                    .sorted(Comparator.comparingLong(this::releaseSequence).reversed()).toList();
            int keptNonCurrent = 0;
            for (Path version : versions) {
                if (version.getFileName().toString().equals(current)) continue;
                if (keptNonCurrent++ < 2) continue;
                deleteTree(version);
            }
        }
    }

    /** 读取 release 的发布序号用于保留排序；损坏或老格式版本按序号零处理。 */
    private long releaseSequence(Path release) {
        try {
            return objectMapper.readValue(release.resolve("manifest.json").toFile(), ArtifactManifest.class).sequence();
        } catch (Exception e) {
            return 0;
        }
    }

    /** 使用目标同目录临时文件和原子替换写入关键文本，失败时尽力清理临时文件。 */
    private void writeStringAtomically(Path target, String value, String temporaryName) throws Exception {
        Files.createDirectories(target.getParent());
        Path temporary = target.resolveSibling(temporaryName);
        Files.deleteIfExists(temporary);
        try {
            Files.writeString(temporary, value, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
            Files.move(temporary, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
        } finally {
            Files.deleteIfExists(temporary);
        }
    }

    /** 从磁盘重读候选文件并核对摘要，防止写入内容与校验内容不一致。 */
    private void verifyStaging(Path staging, Map<String, String> expected) throws Exception {
        for (var entry : expected.entrySet()) {
            String actual = sha256(Files.readString(staging.resolve(entry.getKey())));
            if (!entry.getValue().equals(actual)) throw new IllegalStateException("候选文件摘要不一致: " + entry.getKey());
        }
    }

    /** 计算三个业务文件的稳定 SHA-256 摘要。 */
    private Map<String, String> hashes(MultiFileCodeResult result) {
        Map<String, String> values = new LinkedHashMap<>();
        values.put("index.html", sha256(result.getHtmlCode())); values.put("style.css", sha256(result.getCssCode()));
        values.put("script.js", sha256(result.getJsCode())); return Map.copyOf(values);
    }

    /** 计算 UTF-8 文本的 SHA-256 十六进制摘要。 */
    private String sha256(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    /** 以 UTF-8 新建候选文件，禁止静默覆盖已有文件。 */
    private void write(Path path, String value) throws Exception { Files.writeString(path, value, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW); }
    /** 将可空 manifest 字段规范化为空字符串。 */
    private String safe(String value) { return value == null ? "" : value; }
    /** 限制 requestId 字符集，防止版本路径越界。 */
    private void validateRequestId(String value) { if (value == null || !value.matches("[A-Za-z0-9._-]{1,128}")) throw new ArtifactValidationException("REQUEST_ID_INVALID", null, "请求 ID 非法"); }
    /** 仅递归删除调用方已限定在 staging 或 release 下的目标目录。 */
    private void deleteTree(Path path) throws Exception { if (!Files.exists(path)) return; try (var s=Files.walk(path)){ for(Path p:s.sorted(Comparator.reverseOrder()).toList()) Files.deleteIfExists(p); } }

    /** 持久发布墓碑仅保存防重放所需的请求标识、单调序号和文件摘要。 */
    private record PublishedRequest(String requestId, long sequence, Map<String, String> hashes) { }
}
