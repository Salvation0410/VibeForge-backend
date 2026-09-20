package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.constant.AppConstant;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Comparator;

/** 统一解析应用当前已提交版本，并兼容升级前的平铺目录。 */
@Slf4j
@Component
public class ArtifactPathResolver {
    private final Path outputRoot;
    private final ObjectMapper objectMapper;

    @Autowired
    public ArtifactPathResolver(ObjectMapper objectMapper) {
        this(Path.of(AppConstant.CODE_OUTPUT_ROOT_DIR), objectMapper);
    }

    ArtifactPathResolver(Path outputRoot, ObjectMapper objectMapper) {
        this.outputRoot = outputRoot.toAbsolutePath().normalize();
        this.objectMapper = objectMapper;
    }

    /**
     * 返回应用当前可读取目录；多文件应用优先使用 `.current` 指向的不可变版本。
     *
     * @param codeGenType 生成类型
     * @param appId 应用 ID
     * @return 当前可预览、部署或下载的目录
     */
    public Path resolveActiveRoot(CodeGenTypeEnum codeGenType, long appId) {
        Path root = projectRoot(codeGenType, appId);
        if (codeGenType != CodeGenTypeEnum.MULTI_FILE) return root;
        Path current = resolvePointer(root);
        if (current != null) return current;
        Path fallback = newestValidRelease(root);
        return fallback == null ? root : fallback;
    }

    /**
     * 将静态资源目录名解析为当前活动版本，拒绝目录穿越和未知命名。
     *
     * @param directoryName URL 中的应用目录名
     * @return 可安全读取的活动目录
     */
    public Path resolveDirectoryName(String directoryName) {
        if (directoryName == null || !directoryName.matches("(?:html|multi_file|vue_project)_\\d+")) {
            throw new IllegalArgumentException("非法的应用目录名");
        }
        int separator = directoryName.lastIndexOf('_');
        long appId = Long.parseLong(directoryName.substring(separator + 1));
        CodeGenTypeEnum type = directoryName.startsWith("multi_file_") ? CodeGenTypeEnum.MULTI_FILE
                : directoryName.startsWith("html_") ? CodeGenTypeEnum.HTML : CodeGenTypeEnum.VUE_PROJECT;
        return resolveActiveRoot(type, appId);
    }

    Path projectRoot(CodeGenTypeEnum type, long appId) {
        return outputRoot.resolve(type.getValue() + "_" + appId).normalize();
    }

    private Path resolvePointer(Path root) {
        try {
            Path pointer = root.resolve(".current");
            if (!Files.isRegularFile(pointer)) return null;
            String version = Files.readString(pointer).trim();
            if (!version.matches("[A-Za-z0-9._-]{1,128}")) return null;
            Path release = root.resolve(".releases").resolve(version).normalize();
            return isValidRelease(release) ? release : null;
        } catch (Exception e) {
            log.warn("解析当前产物指针失败, root={}", root, e);
            return null;
        }
    }

    /** 指针损坏时选择最近的完整版本，避免预览直接中断。 */
    private Path newestValidRelease(Path root) {
        Path releases = root.resolve(".releases");
        if (!Files.isDirectory(releases)) return null;
        try (var stream = Files.list(releases)) {
            return stream.filter(this::isValidRelease)
                    .max(Comparator.comparingLong(this::lastModified)).orElse(null);
        } catch (Exception e) {
            log.warn("查找回退产物版本失败, root={}", root, e);
            return null;
        }
    }

    private boolean isValidRelease(Path release) {
        if (!Files.isDirectory(release) || !Files.isRegularFile(release.resolve("manifest.json"))) return false;
        try {
            objectMapper.readValue(release.resolve("manifest.json").toFile(), ArtifactManifest.class);
            return Files.isRegularFile(release.resolve("index.html")) && Files.isRegularFile(release.resolve("style.css"))
                    && Files.isRegularFile(release.resolve("script.js"));
        } catch (Exception e) { return false; }
    }

    private long lastModified(Path path) {
        try { return Files.getLastModifiedTime(path).toMillis(); }
        catch (Exception e) { return 0L; }
    }
}
