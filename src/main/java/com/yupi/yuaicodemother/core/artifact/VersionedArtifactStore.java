package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;

import java.nio.charset.StandardCharsets;
import java.nio.file.*;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.*;

/** 负责不可变产物版本的落盘、指针切换、防重放和保留清理。 */
public class VersionedArtifactStore {
    private final ArtifactPathResolver resolver;
    private final ObjectMapper objectMapper;

    public VersionedArtifactStore(ArtifactPathResolver resolver, ObjectMapper objectMapper) {
        this.resolver = resolver;
        this.objectMapper = objectMapper;
    }

    /**
     * 将已校验文件写入不可变 release，并在最后原子切换当前指针。
     * verifier 失败或任一磁盘校验失败时不会移动 release，也不会改变旧指针；提交后的清理失败不逆转已提交版本。
     */
    public synchronized ArtifactPublishResult publish(CodeGenTypeEnum type, long appId, String requestId,
                                                      Map<String, String> files, String engine,
                                                      String finishReason, StagingVerifier verifier) throws Exception {
        validateRequestId(requestId);
        Path root = resolver.projectRoot(type, appId);
        Path releases = root.resolve(".releases");
        Path release = releases.resolve(requestId);
        Path tombstone = tombstonePath(root, requestId);
        Map<String, String> hashes = hashes(files);
        Files.createDirectories(releases);
        if (Files.isRegularFile(tombstone) && !Files.exists(release)) {
            throw conflict("该请求已发布但产物版本已被保留策略清理，禁止重建或回滚");
        }
        if (Files.exists(release)) return existingResult(release, requestId, hashes);
        long sequence = nextPublicationSequence(root);
        Path staging = root.resolve(".staging").resolve(requestId);
        deleteTree(staging);
        Files.createDirectories(staging);
        for (var entry : files.entrySet()) write(staging.resolve(entry.getKey()), entry.getValue());
        ArtifactManifest manifest = new ArtifactManifest(requestId, appId, safe(engine), Instant.now(), sequence,
                hashes, safe(finishReason), 1);
        objectMapper.writeValue(staging.resolve("manifest.json").toFile(), manifest);
        verifyStaging(staging, hashes);
        if (verifier != null) verifier.verify(staging);
        Files.move(staging, release, StandardCopyOption.ATOMIC_MOVE);
        persistTombstone(root, manifest);
        switchPointer(root, requestId);
        try { cleanupOldReleases(root, requestId); } catch (Exception ignored) { }
        return new ArtifactPublishResult(true, requestId, hashes);
    }

    /** 复核已有版本并按序号恢复指针；发现摘要或墓碑不一致时拒绝重放。 */
    private ArtifactPublishResult existingResult(Path release, String requestId, Map<String, String> hashes) throws Exception {
        ArtifactManifest manifest;
        Map<String, String> diskHashes;
        try {
            manifest = objectMapper.readValue(release.resolve("manifest.json").toFile(), ArtifactManifest.class);
            diskHashes = hashesFromRelease(release, manifest.hashes());
        } catch (Exception e) { throw conflict("相同请求 ID 的已有版本不完整或无法读取"); }
        if (!requestId.equals(manifest.requestId()) || !hashes.equals(manifest.hashes()) || !hashes.equals(diskHashes))
            throw conflict("相同请求 ID 对应不同产物或磁盘版本已被篡改");
        Path root = release.getParent().getParent();
        validateTombstoneIfPresent(root, manifest);
        Path pointer = root.resolve(".current");
        if (!Files.isRegularFile(pointer)) {
            ensureRecoverableSequence(manifest); persistTombstone(root, manifest); switchPointer(root, requestId);
        } else if (!requestId.equals(Files.readString(pointer, StandardCharsets.UTF_8).trim())) {
            ensureTargetSequenceIsNewer(root, manifest); persistTombstone(root, manifest); switchPointer(root, requestId);
        } else persistTombstone(root, manifest);
        return new ArtifactPublishResult(true, requestId, hashes);
    }

    /** 按 manifest 的哈希键读取业务文件，manifest 不得声明目录穿越路径。 */
    private Map<String, String> hashesFromRelease(Path release, Map<String, String> expected) throws Exception {
        Map<String, String> values = new LinkedHashMap<>();
        for (String name : expected.keySet()) {
            if (!safeFileName(name)) throw new IllegalStateException("manifest 文件名非法");
            Path file = release.resolve(name).normalize();
            if (!file.getParent().equals(release) || !Files.isRegularFile(file)) throw new NoSuchFileException(name);
            values.put(name, sha256(Files.readString(file, StandardCharsets.UTF_8)));
        }
        return Map.copyOf(values);
    }

    /** 校验 staging 文件内容与待发布摘要完全一致，避免校验后内容被替换。 */
    private void verifyStaging(Path staging, Map<String, String> expected) throws Exception {
        for (var e : expected.entrySet()) {
            if (!safeFileName(e.getKey()) || !e.getValue().equals(sha256(Files.readString(staging.resolve(e.getKey()), StandardCharsets.UTF_8))))
                throw new IllegalStateException("候选文件摘要不一致: " + e.getKey());
        }
    }

    /** 计算待发布文件的稳定 SHA-256 摘要。 */
    private Map<String, String> hashes(Map<String, String> files) {
        Map<String, String> result = new LinkedHashMap<>();
        files.forEach((name, value) -> { if (!safeFileName(name)) throw new ArtifactValidationException("ARTIFACT_FILE_INVALID", name, "文件名非法"); result.put(name, sha256(value)); });
        return Map.copyOf(result);
    }

    /** 将发布墓碑持久化，墓碑永不随 release 清理，用于永久阻止旧请求重建。 */
    private void persistTombstone(Path root, ArtifactManifest manifest) throws Exception {
        Path path = tombstonePath(root, manifest.requestId());
        PublishedRequest expected = new PublishedRequest(manifest.requestId(), manifest.sequence(), manifest.hashes());
        if (Files.exists(path)) { if (!expected.equals(objectMapper.readValue(path.toFile(), PublishedRequest.class))) throw conflict("发布墓碑与已有版本不一致"); return; }
        Files.createDirectories(path.getParent()); Path temp = path.resolveSibling("." + path.getFileName() + ".tmp"); Files.deleteIfExists(temp);
        try { objectMapper.writeValue(temp.toFile(), expected); Files.move(temp, path, StandardCopyOption.ATOMIC_MOVE); } finally { Files.deleteIfExists(temp); }
    }

    /** 已有墓碑存在时核对序号和摘要，拒绝不一致版本。 */
    private void validateTombstoneIfPresent(Path root, ArtifactManifest manifest) throws Exception {
        Path path = tombstonePath(root, manifest.requestId()); if (!Files.exists(path)) return;
        PublishedRequest actual; try { actual = objectMapper.readValue(path.toFile(), PublishedRequest.class); } catch (Exception e) { throw conflict("发布墓碑损坏或无法读取"); }
        if (!actual.equals(new PublishedRequest(manifest.requestId(), manifest.sequence(), manifest.hashes()))) throw conflict("发布墓碑与已有版本不一致");
    }

    /** 当前指针缺失时只允许有有效正序号版本恢复。 */
    private void ensureRecoverableSequence(ArtifactManifest manifest) { if (manifest.sequence() <= 0) throw conflict("已有版本缺少有效发布序号，无法证明其恢复顺序"); }
    /** 防止旧版本重放覆盖更新版本。 */
    private void ensureTargetSequenceIsNewer(Path root, ArtifactManifest target) {
        try { String id = Files.readString(root.resolve(".current")).trim(); ArtifactManifest current = objectMapper.readValue(root.resolve(".releases").resolve(id).resolve("manifest.json").toFile(), ArtifactManifest.class); if (current.sequence() <= 0 || target.sequence() <= current.sequence()) throw conflict("该请求版本早于或等于当前活动版本"); }
        catch (ArtifactValidationException e) { throw e; } catch (Exception e) { throw conflict("无法确认当前活动版本早于待恢复版本"); }
    }

    /** 计算并原子递增应用级发布序号，序号文件损坏时失败而不重置。 */
    private long nextPublicationSequence(Path root) throws Exception {
        long persisted = 0; Path file = root.resolve(".publication-sequence");
        if (Files.exists(file)) { try { persisted = Long.parseLong(Files.readString(file).trim()); if (persisted < 0) throw new NumberFormatException(); } catch (Exception e) { throw new IllegalStateException("发布序号文件损坏", e); } }
        long next = Math.addExact(Math.max(persisted, highestKnownSequence(root)), 1); writeStringAtomically(file, Long.toString(next), ".publication-sequence.tmp"); return next;
    }
    /** 扫描 release 和墓碑中的最大序号，保证序号文件丢失后仍单调递增。 */
    private long highestKnownSequence(Path root) throws Exception { long max = 0; Path releases = root.resolve(".releases"); if (Files.isDirectory(releases)) try (var s = Files.list(releases)) { for (Path p : s.filter(Files::isDirectory).toList()) max = Math.max(max, objectMapper.readValue(p.resolve("manifest.json").toFile(), ArtifactManifest.class).sequence()); } Path pub = root.resolve(".published"); if (Files.isDirectory(pub)) try (var s = Files.list(pub)) { for (Path p : s.filter(Files::isRegularFile).toList()) max = Math.max(max, objectMapper.readValue(p.toFile(), PublishedRequest.class).sequence()); } return max; }
    /** 使用临时文件原子更新当前版本指针。 */
    private void switchPointer(Path root, String id) throws Exception { writeStringAtomically(root.resolve(".current"), id, ".current.tmp-" + id); }
    /** 保留当前和最近两个历史版本；清理失败不影响已经提交的版本。 */
    private void cleanupOldReleases(Path root, String current) throws Exception { try (var s = Files.list(root.resolve(".releases"))) { int kept = 0; for (Path p : s.filter(Files::isDirectory).sorted(Comparator.comparingLong(this::releaseSequence).reversed()).toList()) { if (p.getFileName().toString().equals(current)) continue; if (kept++ < 2) continue; deleteTree(p); } } }
    /** 读取版本序号用于保留排序，损坏版本按零处理。 */
    private long releaseSequence(Path p) { try { return objectMapper.readValue(p.resolve("manifest.json").toFile(), ArtifactManifest.class).sequence(); } catch (Exception e) { return 0; } }
    /** 原子写文本并在失败后清理临时文件。 */
    private void writeStringAtomically(Path target, String value, String tempName) throws Exception { Files.createDirectories(target.getParent()); Path temp = target.resolveSibling(tempName); Files.deleteIfExists(temp); try { Files.writeString(temp, value, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW); Files.move(temp, target, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING); } finally { Files.deleteIfExists(temp); } }
    /** 写入新文件，禁止覆盖 staging 中已有内容。 */
    private void write(Path path, String value) throws Exception { Files.createDirectories(path.getParent()); Files.writeString(path, value == null ? "" : value, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW); }
    /** 递归删除调用方限定的 staging 或过期 release。 */
    private void deleteTree(Path path) throws Exception { if (!Files.exists(path)) return; try (var s = Files.walk(path)) { for (Path p : s.sorted(Comparator.reverseOrder()).toList()) Files.deleteIfExists(p); } }
    private Path tombstonePath(Path root, String id) { return root.resolve(".published").resolve(id + ".json"); }
    private ArtifactValidationException conflict(String message) { return new ArtifactValidationException("ARTIFACT_VERSION_CONFLICT", null, message); }
    private void validateRequestId(String value) { if (value == null || !value.matches("[A-Za-z0-9._-]{1,128}")) throw new ArtifactValidationException("REQUEST_ID_INVALID", null, "请求 ID 非法"); }
    private boolean safeFileName(String name) { return name != null && name.matches("[A-Za-z0-9._-]+") && !name.contains(".."); }
    private String safe(String value) { return value == null ? "" : value; }
    private String sha256(String value) { try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); } catch (Exception e) { throw new IllegalStateException(e); } }
    @FunctionalInterface public interface StagingVerifier { void verify(Path staging) throws Exception; }
    private record PublishedRequest(String requestId, long sequence, Map<String, String> hashes) { }
}
