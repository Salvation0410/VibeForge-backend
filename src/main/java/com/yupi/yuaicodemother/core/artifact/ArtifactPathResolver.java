package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.constant.AppConstant;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Comparator;
import java.util.HexFormat;
import java.util.Map;
import java.util.Set;

/** 统一解析应用当前已提交版本，并兼容升级前的平铺目录。 */
@Slf4j
@Component
public class ArtifactPathResolver {
    private final Path outputRoot;
    private final ObjectMapper objectMapper;

    /** 使用生产代码输出根目录创建活动版本解析器。 */
    @Autowired
    public ArtifactPathResolver(ObjectMapper objectMapper) {
        this(Path.of(AppConstant.CODE_OUTPUT_ROOT_DIR), objectMapper);
    }

    /** 使用指定输出根目录创建解析器，供隔离测试复用。 */
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
        // HTML 与多文件都使用不可变 release；没有新版本时仍回退到旧平铺目录。
        if (codeGenType != CodeGenTypeEnum.MULTI_FILE && codeGenType != CodeGenTypeEnum.HTML) return root;
        Path current = resolvePointer(root);
        if (current != null) return current;
        if (!Files.exists(root.resolve(".current"))) return root;
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

    /** 根据生成类型和应用 ID 计算兼容旧结构的应用根目录。 */
    Path projectRoot(CodeGenTypeEnum type, long appId) {
        return outputRoot.resolve(type.getValue() + "_" + appId).normalize();
    }

    /** 读取并校验 `.current` 指针，非法或不完整版本返回空。 */
    private Path resolvePointer(Path root) {
        try {
            Path pointer = root.resolve(".current");
            if (!Files.isRegularFile(pointer)) return null;
            String version = Files.readString(pointer).trim();
            if (!version.matches("[A-Za-z0-9._-]{1,128}")) return null;
            Path release = root.resolve(".releases").resolve(version).normalize();
            return isValidRelease(root, release) ? release : null;
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
            return stream.filter(release -> isValidRelease(root, release))
                    .max(Comparator.comparingLong(this::releaseSequence)).orElse(null);
        } catch (Exception e) {
            log.warn("查找回退产物版本失败, root={}", root, e);
            return null;
        }
    }

    /** 按生成类型校验必要文件集合并重算每个 SHA-256；任何缺失、额外声明或篡改都会拒绝激活。 */
    private boolean isValidRelease(Path root, Path release) {
        if (!Files.isDirectory(release) || !Files.isRegularFile(release.resolve("manifest.json"))) return false;
        try {
            ArtifactManifest manifest = objectMapper.readValue(release.resolve("manifest.json").toFile(), ArtifactManifest.class);
            long expectedAppId = Long.parseLong(root.getFileName().toString().substring(root.getFileName().toString().lastIndexOf('_') + 1));
            if (manifest.sequence() <= 0 || manifest.appId() != expectedAppId
                    || !release.getFileName().toString().equals(manifest.requestId())) return false;
            Set<String> required = requiredFiles(root);
            if (manifest.hashes() == null || !manifest.hashes().keySet().equals(required)) return false;
            for (Map.Entry<String, String> entry : manifest.hashes().entrySet()) {
                String fileName = entry.getKey();
                if (fileName == null || !fileName.matches("[A-Za-z0-9._-]+") || fileName.contains("..")
                        || !Files.isRegularFile(release.resolve(fileName).normalize())
                        || !release.equals(release.resolve(fileName).normalize().getParent())) return false;
                String actual = sha256(Files.readString(release.resolve(fileName), StandardCharsets.UTF_8));
                if (!actual.equals(entry.getValue())) return false;
            }
            return true;
        } catch (Exception e) { return false; }
    }

    /** 根据项目根目录类型返回 manifest 必须精确声明的业务文件。 */
    private Set<String> requiredFiles(Path root) {
        String name = root.getFileName().toString();
        return name.startsWith("html_") ? Set.of("index.html")
                : Set.of("index.html", "style.css", "script.js");
    }

    /** 读取 manifest 发布序号用于损坏指针回退，失败或非正序号不参与较新版本竞争。 */
    private long releaseSequence(Path path) {
        try {
            long sequence = objectMapper.readValue(path.resolve("manifest.json").toFile(), ArtifactManifest.class).sequence();
            return Math.max(sequence, 0L);
        }
        catch (Exception e) { return 0L; }
    }

    /** 计算 UTF-8 文件正文的 SHA-256，用于拒绝 manifest 与磁盘内容不一致的 release。 */
    private String sha256(String value) {
        try { return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8))); }
        catch (Exception e) { throw new IllegalStateException(e); }
    }
}
