package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.config.HtmlArtifactProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

/** 在调用模型前限制大型单文件 HTML 的全量重写，避免输出截断并保留当前可用版本。 */
@Component
@RequiredArgsConstructor
public class HtmlOutputBudgetGuard {
    private static final String FILE_NAME = "index.html";

    private final ArtifactPathResolver artifactPathResolver;
    private final HtmlArtifactProperties properties;

    /**
     * 检查应用当前 HTML 是否仍适合全量重写。
     * 非 HTML 类型和没有历史产物的首次生成直接放行；超过阈值时在历史入库及模型调用前失败。
     *
     * @param type 当前应用生成类型
     * @param appId 应用 ID
     */
    public void checkRewriteAllowed(CodeGenTypeEnum type, long appId) {
        if (type != CodeGenTypeEnum.HTML) return;

        Path indexFile = artifactPathResolver.resolveActiveRoot(type, appId).resolve(FILE_NAME);
        if (!Files.isRegularFile(indexFile)) return;

        try {
            int sourceChars = Files.readString(indexFile, StandardCharsets.UTF_8).length();
            if (sourceChars > properties.getMaxRewriteSourceChars()) {
                throw new ArtifactValidationException("HTML_OUTPUT_BUDGET_EXCEEDED", FILE_NAME,
                        "当前单文件页面过大，请迁移为多文件应用后继续优化");
            }
        } catch (ArtifactValidationException e) {
            throw e;
        } catch (IOException e) {
            throw new ArtifactValidationException("HTML_OUTPUT_BUDGET_CHECK_FAILED", FILE_NAME,
                    "无法读取当前单文件页面，已停止本次优化");
        }
    }
}
