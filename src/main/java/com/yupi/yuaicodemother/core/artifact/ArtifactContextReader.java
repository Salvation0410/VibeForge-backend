package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** 以有界、结构化形式读取应用当前活动产物，供 Python 工作流准备模型上下文。 */
@Component
@RequiredArgsConstructor
public class ArtifactContextReader {
    static final int MAX_STATIC_ARTIFACT_CHARS = 100_000;
    static final int MAX_VUE_ENTRIES = 200;

    private final ArtifactPathResolver artifactPathResolver;

    /**
     * 读取当前活动产物。静态类型返回完整生成协议，Vue 只返回可供模型选择性读取的文件清单。
     */
    public Map<String, Object> read(CodeGenTypeEnum type, long appId) {
        Path activeRoot = artifactPathResolver.resolveActiveRoot(type, appId);
        if (!Files.isDirectory(activeRoot)) {
            return Map.of("exists", false, "codeGenType", type.name());
        }
        return type == CodeGenTypeEnum.VUE_PROJECT
                ? readVueContext(type, activeRoot)
                : readStaticContext(type, activeRoot);
    }

    private Map<String, Object> readStaticContext(CodeGenTypeEnum type, Path activeRoot) {
        String artifact = type == CodeGenTypeEnum.HTML
                ? htmlArtifact(readRequired(activeRoot, "index.html"))
                : multiFileArtifact(activeRoot);
        if (artifact.length() > MAX_STATIC_ARTIFACT_CHARS) {
            throw new BusinessException(ErrorCode.OPERATION_ERROR,
                    "ARTIFACT_CONTEXT_TOO_LARGE: active artifact exceeds 100000 characters");
        }
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("exists", true);
        result.put("codeGenType", type.name());
        if (type == CodeGenTypeEnum.HTML) result.put("entry", "index.html");
        result.put("artifact", artifact);
        return Map.copyOf(result);
    }

    private Map<String, Object> readVueContext(CodeGenTypeEnum type, Path activeRoot) {
        try (var paths = Files.walk(activeRoot)) {
            List<String> entries = paths
                    .filter(Files::isRegularFile)
                    .map(activeRoot::relativize)
                    .filter(this::isVisibleVueSource)
                    .map(this::portablePath)
                    .sorted()
                    .limit(MAX_VUE_ENTRIES + 1L)
                    .toList();
            boolean truncated = entries.size() > MAX_VUE_ENTRIES;
            if (truncated) entries = List.copyOf(entries.subList(0, MAX_VUE_ENTRIES));
            return Map.of(
                    "exists", true,
                    "codeGenType", type.name(),
                    "entries", entries,
                    "truncated", truncated);
        } catch (Exception ignored) {
            throw readFailure("Vue project file list");
        }
    }

    private boolean isVisibleVueSource(Path relativePath) {
        for (Path segment : relativePath) {
            String value = segment.toString();
            if (value.startsWith(".") || "node_modules".equals(value) || "dist".equals(value)) return false;
        }
        return true;
    }

    private String multiFileArtifact(Path activeRoot) {
        return "index.html\n```html\n" + readRequired(activeRoot, "index.html") + "\n```\n"
                + "style.css\n```css\n" + readRequired(activeRoot, "style.css") + "\n```\n"
                + "script.js\n```javascript\n" + readRequired(activeRoot, "script.js") + "\n```";
    }

    private String htmlArtifact(String html) {
        return "```html\n" + html + "\n```";
    }

    private String readRequired(Path root, String fileName) {
        Path file = root.resolve(fileName).normalize();
        if (!file.getParent().equals(root) || !Files.isRegularFile(file)) {
            throw new BusinessException(ErrorCode.OPERATION_ERROR,
                    "ARTIFACT_CONTEXT_INVALID: required active artifact file is missing: " + fileName);
        }
        try {
            return Files.readString(file, StandardCharsets.UTF_8);
        } catch (Exception ignored) {
            throw readFailure(fileName);
        }
    }

    private String portablePath(Path path) {
        return path.toString().replace('\\', '/');
    }

    private BusinessException readFailure(String target) {
        return new BusinessException(ErrorCode.OPERATION_ERROR,
                "ARTIFACT_CONTEXT_READ_FAILED: failed to read " + target);
    }
}
