package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.ai.model.HtmlCodeResult;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 严格解析单文件 HTML 候选产物，仅接受唯一闭合的 html 围栏或边界完整的纯 HTML。
 */
public class HtmlArtifactParser {

    private static final String ERROR_CODE = "HTML_FORMAT_INVALID";
    private static final String FILE_NAME = "index.html";
    private static final String ERROR_MESSAGE = "HTML 响应必须是唯一且完整的文档";

    private static final Pattern FENCED_HTML_PATTERN = Pattern.compile(
            "\\A\\s*```html[ \\t]*\\R(?<document>(?:(?!```)[\\s\\S])*)\\R```[ \\t]*\\s*\\z",
            Pattern.CASE_INSENSITIVE);
    private static final Pattern COMPLETE_HTML_PATTERN = Pattern.compile(
            "\\A(?:<!doctype\\s+html\\s*>|<html(?:\\s|>))[\\s\\S]*</html>\\z",
            Pattern.CASE_INSENSITIVE);

    /**
     * 从模型原始响应中提取唯一完整的 HTML 文档。
     *
     * @param rawArtifact 模型返回的原始候选文本
     * @return 仅包含完整 HTML 的解析结果
     * @throws ArtifactValidationException 输入为空、存在围栏外文本、重复区块、围栏未闭合或文档边界不完整时抛出；失败时不返回部分结果
     */
    public HtmlCodeResult parse(String rawArtifact) {
        if (rawArtifact == null || rawArtifact.isBlank()) {
            throw invalidFormat();
        }

        String candidate = rawArtifact.strip();
        Matcher fencedMatcher = FENCED_HTML_PATTERN.matcher(candidate);
        String document = fencedMatcher.matches()
                ? fencedMatcher.group("document").strip()
                : candidate;

        if (!COMPLETE_HTML_PATTERN.matcher(document).matches()) {
            throw invalidFormat();
        }

        HtmlCodeResult result = new HtmlCodeResult();
        result.setHtmlCode(document);
        return result;
    }

    /** 统一构造稳定错误码和文件定位，调用方不会收到任何部分解析结果。 */
    private ArtifactValidationException invalidFormat() {
        return new ArtifactValidationException(ERROR_CODE, FILE_NAME, ERROR_MESSAGE);
    }
}
