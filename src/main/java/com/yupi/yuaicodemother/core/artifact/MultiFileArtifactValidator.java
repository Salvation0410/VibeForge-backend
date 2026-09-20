package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;
import org.springframework.stereotype.Component;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/** 对已解析的三文件候选产物执行不依赖模型的硬校验。 */
@Component
public class MultiFileArtifactValidator {
    private static final Pattern STYLE_LINK = Pattern.compile("(?is)<link\\b(?=[^>]*rel=[\\\"']stylesheet[\\\"'])(?=[^>]*href=[\\\"'](?:\\./)?style\\.css[\\\"'])[^>]*>");
    private static final Pattern SCRIPT_LINK = Pattern.compile("(?is)<script\\b(?=[^>]*src=[\\\"'](?:\\./)?script\\.js[\\\"'])[^>]*>\\s*</script>");
    private static final Pattern INLINE_SCRIPT = Pattern.compile("(?is)<script(?![^>]*\\bsrc=)[^>]*>.*?</script>");

    /**
     * 校验 HTML 引用、CSS 规则和 JavaScript 基础结构，并一次返回全部错误。
     *
     * @param artifact 已按严格协议解析的三文件内容
     * @return 包含是否通过及全部错误的结果
     */
    public ArtifactValidationResult validate(MultiFileCodeResult artifact) {
        List<ArtifactValidationError> errors = new ArrayList<>();
        if (artifact == null) {
            errors.add(error("ARTIFACT_MISSING", null, "多文件产物不能为空"));
            return new ArtifactValidationResult(false, errors);
        }
        validateHtml(artifact.getHtmlCode(), errors);
        validateCss(artifact.getCssCode(), errors);
        validateJavascript(artifact.getJsCode(), errors);
        return new ArtifactValidationResult(errors.isEmpty(), errors);
    }

    /**
     * 执行硬校验并在失败时中止发布，异常保留首个稳定错误码。
     *
     * @param artifact 已解析的候选产物
     * @throws ArtifactValidationException 任一确定性规则不满足时抛出
     */
    public void validateOrThrow(MultiFileCodeResult artifact) {
        ArtifactValidationResult result = validate(artifact);
        if (!result.valid()) {
            ArtifactValidationError first = result.errors().getFirst();
            throw new ArtifactValidationException(first.code(), first.file(), first.message());
        }
    }

    /** 校验 HTML 文档结构、外链资源和内联代码限制。 */
    private void validateHtml(String html, List<ArtifactValidationError> errors) {
        String value = safe(html);
        String lower = value.toLowerCase(Locale.ROOT);
        if (!(lower.contains("<html") && lower.contains("</html>") && lower.contains("<head")
                && lower.contains("</head>") && lower.contains("<body") && lower.contains("</body>"))) {
            errors.add(error("HTML_STRUCTURE_INVALID", "index.html", "HTML 缺少完整的 html、head 或 body 结构"));
        }
        if (!STYLE_LINK.matcher(value).find()) errors.add(error("HTML_STYLESHEET_MISSING", "index.html", "HTML 未引用 style.css"));
        if (!SCRIPT_LINK.matcher(value).find()) errors.add(error("HTML_SCRIPT_MISSING", "index.html", "HTML 未引用 script.js"));
        if (lower.contains("<style")) errors.add(error("HTML_INLINE_STYLE_FORBIDDEN", "index.html", "HTML 不允许包含内联 style 区块"));
        if (INLINE_SCRIPT.matcher(value).find()) errors.add(error("HTML_INLINE_SCRIPT_FORBIDDEN", "index.html", "HTML 不允许包含内联脚本"));
        rejectMarkdown(value, "index.html", errors);
    }

    /** 校验 CSS 至少包含一个闭合规则块且不是标题文本。 */
    private void validateCss(String css, List<ArtifactValidationError> errors) {
        String value = safe(css);
        if (!value.contains("{") || !value.contains("}") || !delimitersBalanced(value, true)) {
            errors.add(error("CSS_STRUCTURE_INVALID", "style.css", "CSS 规则块不完整"));
        }
        if (isFilenameOnly(value)) errors.add(error("CSS_CONTENT_INVALID", "style.css", "CSS 内容不能是文件名或标题"));
        rejectMarkdown(value, "style.css", errors);
    }

    /** 校验 JavaScript 含可执行语句且基础分隔符闭合。 */
    private void validateJavascript(String js, List<ArtifactValidationError> errors) {
        String value = safe(js);
        if (isFilenameOnly(value)) errors.add(error("JS_CONTENT_INVALID", "script.js", "JavaScript 内容不能是文件名或标题"));
        if (!value.matches("(?s).*[;=(){}\\[\\]].*")) errors.add(error("JS_CONTENT_INVALID", "script.js", "JavaScript 缺少可执行语句"));
        if (!delimitersBalanced(value, false)) errors.add(error("JS_STRUCTURE_INVALID", "script.js", "JavaScript 分隔符不完整"));
        rejectMarkdown(value, "script.js", errors);
    }

    /** 扫描代码分隔符，跳过引号和注释，避免因字符串内容产生误报。 */
    private boolean delimitersBalanced(String value, boolean cssMode) {
        Deque<Character> stack = new ArrayDeque<>();
        char quote = 0;
        boolean lineComment = false, blockComment = false, escaped = false;
        for (int i = 0; i < value.length(); i++) {
            char c = value.charAt(i), next = i + 1 < value.length() ? value.charAt(i + 1) : 0;
            if (lineComment) { if (c == '\n') lineComment = false; continue; }
            if (blockComment) { if (c == '*' && next == '/') { blockComment = false; i++; } continue; }
            if (quote != 0) { if (escaped) escaped = false; else if (c == '\\') escaped = true; else if (c == quote) quote = 0; continue; }
            if (c == '/' && next == '*') { blockComment = true; i++; continue; }
            if (!cssMode && c == '/' && next == '/') { lineComment = true; i++; continue; }
            if (c == '\'' || c == '"' || (!cssMode && c == '`')) { quote = c; continue; }
            if (c == '{' || (!cssMode && (c == '(' || c == '['))) stack.push(c);
            else if (c == '}' || (!cssMode && (c == ')' || c == ']'))) {
                if (stack.isEmpty() || !matches(stack.pop(), c)) return false;
            }
        }
        return stack.isEmpty() && quote == 0 && !blockComment;
    }

    /** 判断一对代码分隔符是否对应。 */
    private boolean matches(char open, char close) {
        return open == '{' && close == '}' || open == '(' && close == ')' || open == '[' && close == ']';
    }

    /** 拒绝被解析进文件正文的 Markdown 围栏。 */
    private void rejectMarkdown(String value, String file, List<ArtifactValidationError> errors) {
        if (value.contains("```")) errors.add(error("MARKDOWN_RESIDUE", file, "文件内容包含 Markdown 围栏"));
    }

    /** 识别被误当作正文的文件名或章节标题。 */
    private boolean isFilenameOnly(String value) {
        String normalized = value.trim().toLowerCase(Locale.ROOT);
        return normalized.matches("(?:index\\.html|style\\.css|script\\.js|html|css|javascript|js)");
    }

    /** 将可空文件内容规范化为空字符串。 */
    private String safe(String value) { return value == null ? "" : value; }
    /** 创建带稳定错误码的校验错误。 */
    private ArtifactValidationError error(String code, String file, String message) { return new ArtifactValidationError(code, file, message); }
}
