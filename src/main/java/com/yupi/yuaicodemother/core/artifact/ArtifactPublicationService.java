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
        Map<String, String> hashes = hashes(artifact);
        try {
            Files.createDirectories(releases);
            if (Files.exists(release)) return existingResult(release, requestId, hashes);
            Path staging = root.resolve(".staging").resolve(requestId);
            deleteTree(staging);
            Files.createDirectories(staging);
            write(staging.resolve("index.html"), artifact.getHtmlCode());
            write(staging.resolve("style.css"), artifact.getCssCode());
            write(staging.resolve("script.js"), artifact.getJsCode());
            ArtifactManifest manifest = new ArtifactManifest(requestId, appId, safe(engine), Instant.now(),
                    hashes, safe(finishReason), 1);
            objectMapper.writeValue(staging.resolve("manifest.json").toFile(), manifest);
            verifyStaging(staging, hashes);
            Files.move(staging, release, StandardCopyOption.ATOMIC_MOVE);
            switchPointer(root, requestId);
            try { cleanupOldReleases(root, requestId); }
            catch (Exception ignored) { /* 清理失败不改变已经原子提交的成功结果。 */ }
            return new ArtifactPublishResult(true, requestId, hashes);
        } catch (ArtifactValidationException e) { throw e; }
        catch (Exception e) {
            throw new ArtifactValidationException("ARTIFACT_PUBLISH_FAILED", null, "产物发布失败: " + e.getMessage());
        }
    }

    private ArtifactPublishResult existingResult(Path release, String requestId, Map<String, String> hashes) throws Exception {
        ArtifactManifest manifest = objectMapper.readValue(release.resolve("manifest.json").toFile(), ArtifactManifest.class);
        if (!hashes.equals(manifest.hashes())) throw new ArtifactValidationException("ARTIFACT_VERSION_CONFLICT", null, "相同请求 ID 对应不同产物");
        Path pointer = release.getParent().getParent().resolve(".current");
        if (!Files.isRegularFile(pointer)) switchPointer(pointer.getParent(), requestId);
        else if (!requestId.equals(Files.readString(pointer).trim()))
            throw new ArtifactValidationException("ARTIFACT_VERSION_CONFLICT", null, "该请求版本已存在但不是当前版本");
        return new ArtifactPublishResult(true, requestId, hashes);
    }

    /** 使用同目录临时文件原子替换当前指针，避免读到半写入内容。 */
    private void switchPointer(Path root, String requestId) throws Exception {
        Path temporary = root.resolve(".current.tmp-" + requestId);
        Files.deleteIfExists(temporary);
        Files.writeString(temporary, requestId, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW);
        Files.move(temporary, root.resolve(".current"), StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
    }

    /** 仅清理非当前的旧版本，最终保留当前版本和最近两个成功版本。 */
    private void cleanupOldReleases(Path root, String current) throws Exception {
        try (var stream = Files.list(root.resolve(".releases"))) {
            List<Path> versions = stream.filter(Files::isDirectory)
                    .sorted(Comparator.comparingLong(this::lastModified).reversed()).toList();
            int keptNonCurrent = 0;
            for (Path version : versions) {
                if (version.getFileName().toString().equals(current)) continue;
                if (keptNonCurrent++ < 2) continue;
                deleteTree(version);
            }
        }
    }

    private void verifyStaging(Path staging, Map<String, String> expected) throws Exception {
        for (var entry : expected.entrySet()) {
            String actual = sha256(Files.readString(staging.resolve(entry.getKey())));
            if (!entry.getValue().equals(actual)) throw new IllegalStateException("候选文件摘要不一致: " + entry.getKey());
        }
    }

    private Map<String, String> hashes(MultiFileCodeResult result) {
        Map<String, String> values = new LinkedHashMap<>();
        values.put("index.html", sha256(result.getHtmlCode())); values.put("style.css", sha256(result.getCssCode()));
        values.put("script.js", sha256(result.getJsCode())); return Map.copyOf(values);
    }

    private String sha256(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
    private void write(Path path, String value) throws Exception { Files.writeString(path, value, StandardCharsets.UTF_8, StandardOpenOption.CREATE_NEW); }
    private String safe(String value) { return value == null ? "" : value; }
    private void validateRequestId(String value) { if (value == null || !value.matches("[A-Za-z0-9._-]{1,128}")) throw new ArtifactValidationException("REQUEST_ID_INVALID", null, "请求 ID 非法"); }
    private long lastModified(Path path) { try { return Files.getLastModifiedTime(path).toMillis(); } catch (Exception e) { return 0; } }
    private void deleteTree(Path path) throws Exception { if (!Files.exists(path)) return; try (var s=Files.walk(path)){ for(Path p:s.sorted(Comparator.reverseOrder()).toList()) Files.deleteIfExists(p); } }
}
