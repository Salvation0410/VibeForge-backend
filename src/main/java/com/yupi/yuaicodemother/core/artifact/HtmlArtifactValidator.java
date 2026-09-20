package com.yupi.yuaicodemother.core.artifact;

import com.yupi.yuaicodemother.ai.model.HtmlCodeResult;
import org.springframework.stereotype.Component;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** 对单文件 HTML 候选产物执行不依赖浏览器容错的确定性完整性校验。 */
@Component
public class HtmlArtifactValidator {
    private static final String FILE = "index.html";
    private static final Pattern HTML_OPEN = Pattern.compile("(?is)<html\\b[^>]*>");
    private static final Pattern HTML_CLOSE = Pattern.compile("(?is)</html\\s*>");
    private static final Pattern HEAD_OPEN = Pattern.compile("(?is)<head\\b[^>]*>");
    private static final Pattern HEAD_CLOSE = Pattern.compile("(?is)</head\\s*>");
    private static final Pattern BODY_OPEN = Pattern.compile("(?is)<body\\b[^>]*>");
    private static final Pattern BODY_CLOSE = Pattern.compile("(?is)</body\\s*>");
    private static final Pattern TRAILING_OPERATOR = Pattern.compile(
            "(?s)(?:=>|===|!==|==|!=|<=|>=|&&|\\|\\||\\?\\?|[=+\\-*/%&|^!~<>?:,])$");
    private static final Set<String> REGEX_PREFIX_KEYWORDS = Set.of(
            "return", "throw", "case", "delete", "void", "typeof", "instanceof", "in", "of", "yield", "await", "new");

    /**
     * 校验原始 HTML 文档边界以及内联 CSS、JavaScript 的明确截断，并一次返回全部错误。
     *
     * @param artifact 单文件 HTML 候选产物
     * @return 包含是否通过及全部稳定错误的校验结果
     */
    public ArtifactValidationResult validate(HtmlCodeResult artifact) {
        List<ArtifactValidationError> errors = new ArrayList<>();
        String html = artifact == null || artifact.getHtmlCode() == null ? "" : artifact.getHtmlCode();

        if (!hasOrderedDocumentBoundary(html)) {
            addError(errors, "HTML_DOCUMENT_INCOMPLETE", "HTML 缺少完整且顺序正确的 html、head 或 body 边界");
        }
        if (html.contains("```")) {
            addError(errors, "HTML_MARKDOWN_RESIDUE", "HTML 正文包含 Markdown 围栏残留");
        }

        validateEmbeddedBlocks(html, "style", true, errors);
        validateEmbeddedBlocks(html, "script", false, errors);
        return new ArtifactValidationResult(errors.isEmpty(), errors);
    }

    /**
     * 执行单文件 HTML 硬校验；失败时以统一错误码中止流程，并在文件和消息中保留首个具体错误。
     *
     * @param artifact 单文件 HTML 候选产物
     * @throws ArtifactValidationException 任一确定性完整性规则不满足时抛出
     */
    public void validateOrThrow(HtmlCodeResult artifact) {
        ArtifactValidationResult result = validate(artifact);
        if (!result.valid()) {
            ArtifactValidationError first = result.errors().getFirst();
            throw new ArtifactValidationException("HTML_VALIDATION_FAILED", first.file(),
                    first.code() + ": " + first.message());
        }
    }

    /** 原样检查文档标签是否存在且按 html > head > body 的边界顺序闭合，不借助解析器补齐缺失标签。 */
    private boolean hasOrderedDocumentBoundary(String html) {
        int htmlOpen = start(HTML_OPEN, html, 0);
        int headOpen = start(HEAD_OPEN, html, Math.max(0, htmlOpen));
        int headClose = start(HEAD_CLOSE, html, Math.max(0, headOpen));
        int bodyOpen = start(BODY_OPEN, html, Math.max(0, headClose));
        int bodyClose = start(BODY_CLOSE, html, Math.max(0, bodyOpen));
        int htmlClose = start(HTML_CLOSE, html, Math.max(0, bodyClose));
        return htmlOpen >= 0 && headOpen > htmlOpen && headClose > headOpen
                && bodyOpen > headClose && bodyClose > bodyOpen && htmlClose > bodyClose;
    }

    /** 按原始标签边界提取所有 style/script 区块；缺少结束标签与正文词法截断使用同一稳定错误。 */
    private void validateEmbeddedBlocks(String html, String tag, boolean css, List<ArtifactValidationError> errors) {
        Pattern openPattern = Pattern.compile("(?is)<" + tag + "\\b[^>]*>");
        Pattern closePattern = Pattern.compile("(?is)</" + tag + "\\s*>");
        int cursor = 0;
        boolean invalid = false;
        while (cursor < html.length()) {
            Matcher open = openPattern.matcher(html);
            if (!open.find(cursor)) {
                invalid |= closePattern.matcher(html).find(cursor);
                break;
            }
            Matcher orphanClose = closePattern.matcher(html);
            if (orphanClose.find(cursor) && orphanClose.start() < open.start()) {
                invalid = true;
            }
            Matcher close = closePattern.matcher(html);
            if (!close.find(open.end())) {
                invalid = true;
                break;
            }
            String code = html.substring(open.end(), close.start());
            ScanResult scan = css ? scanCss(code) : scanJavascript(code);
            invalid |= !scan.complete();
            if (!css && isTrailingJavascriptFragment(code, scan)) {
                addError(errors, "HTML_SCRIPT_TRAILING_FRAGMENT", "script 末尾存在未完成的运算符或表达式");
            }
            cursor = close.end();
        }
        if (invalid) {
            addError(errors, css ? "HTML_STYLE_INCOMPLETE" : "HTML_SCRIPT_INCOMPLETE",
                    css ? "style 标签或 CSS 词法结构未闭合" : "script 标签或 JavaScript 词法结构未闭合");
        }
    }

    /**
     * 状态扫描器只判断确定性的词法未闭合：忽略字符串、注释和正则内部的分隔符，并跟踪模板插值边界。
     * 它不尝试替代 JavaScript/CSS 语法解析器，也不把可疑但闭合的业务表达式判为错误。
     */
    private ScanResult scanJavascript(String code) {
        Deque<Delimiter> delimiters = new ArrayDeque<>();
        LexicalState state = LexicalState.CODE;
        boolean escaped = false;
        boolean regexCharacterClass = false;
        boolean regexAllowed = true;
        boolean unfinishedTemplate = false;

        for (int i = 0; i < code.length(); i++) {
            char current = code.charAt(i);
            char next = i + 1 < code.length() ? code.charAt(i + 1) : 0;
            switch (state) {
                case SINGLE_QUOTE, DOUBLE_QUOTE -> {
                    if (escaped) {
                        escaped = false;
                    } else if (current == '\\') {
                        escaped = true;
                    } else if ((state == LexicalState.SINGLE_QUOTE && current == '\'')
                            || (state == LexicalState.DOUBLE_QUOTE && current == '"')) {
                        state = LexicalState.CODE;
                        regexAllowed = false;
                    } else if (current == '\n' || current == '\r') {
                        return ScanResult.incomplete(false);
                    }
                }
                case TEMPLATE -> {
                    if (escaped) {
                        escaped = false;
                    } else if (current == '\\') {
                        escaped = true;
                    } else if (current == '`') {
                        state = LexicalState.CODE;
                        regexAllowed = false;
                    } else if (current == '$' && next == '{') {
                        delimiters.push(new Delimiter('{', true));
                        state = LexicalState.CODE;
                        regexAllowed = true;
                        i++;
                    }
                }
                case LINE_COMMENT -> {
                    if (current == '\n' || current == '\r') {
                        state = LexicalState.CODE;
                    }
                }
                case BLOCK_COMMENT -> {
                    if (current == '*' && next == '/') {
                        state = LexicalState.CODE;
                        i++;
                    }
                }
                case REGEX -> {
                    if (escaped) {
                        escaped = false;
                    } else if (current == '\\') {
                        escaped = true;
                    } else if (current == '[') {
                        regexCharacterClass = true;
                    } else if (current == ']' && regexCharacterClass) {
                        regexCharacterClass = false;
                    } else if (current == '/' && !regexCharacterClass) {
                        while (i + 1 < code.length() && Character.isLetter(code.charAt(i + 1))) {
                            i++;
                        }
                        state = LexicalState.CODE;
                        regexAllowed = false;
                    } else if (current == '\n' || current == '\r') {
                        return ScanResult.incomplete(false);
                    }
                }
                case CODE -> {
                    if (Character.isWhitespace(current)) {
                        continue;
                    }
                    if (current == '/' && next == '/') {
                        state = LexicalState.LINE_COMMENT;
                        i++;
                    } else if (current == '/' && next == '*') {
                        state = LexicalState.BLOCK_COMMENT;
                        i++;
                    } else if (current == '/' && regexAllowed) {
                        state = LexicalState.REGEX;
                        regexCharacterClass = false;
                    } else if (current == '\'') {
                        state = LexicalState.SINGLE_QUOTE;
                    } else if (current == '"') {
                        state = LexicalState.DOUBLE_QUOTE;
                    } else if (current == '`') {
                        state = LexicalState.TEMPLATE;
                    } else if (current == '(' || current == '[' || current == '{') {
                        delimiters.push(new Delimiter(current, false));
                        regexAllowed = true;
                    } else if (current == ')' || current == ']' || current == '}') {
                        if (delimiters.isEmpty() || !matches(delimiters.peek().value(), current)) {
                            return ScanResult.incomplete(false);
                        }
                        Delimiter closed = delimiters.pop();
                        if (closed.templateExpression()) {
                            state = LexicalState.TEMPLATE;
                        }
                        regexAllowed = false;
                    } else if (Character.isJavaIdentifierStart(current)) {
                        int end = i + 1;
                        while (end < code.length() && Character.isJavaIdentifierPart(code.charAt(end))) {
                            end++;
                        }
                        regexAllowed = REGEX_PREFIX_KEYWORDS.contains(code.substring(i, end));
                        i = end - 1;
                    } else if (Character.isDigit(current)) {
                        int end = i + 1;
                        while (end < code.length() && (Character.isLetterOrDigit(code.charAt(end))
                                || code.charAt(end) == '.' || code.charAt(end) == '_')) {
                            end++;
                        }
                        regexAllowed = false;
                        i = end - 1;
                    } else {
                        regexAllowed = isExpressionPrefix(current);
                    }
                }
            }
        }
        unfinishedTemplate = state == LexicalState.TEMPLATE
                || delimiters.stream().anyMatch(Delimiter::templateExpression);
        boolean complete = (state == LexicalState.CODE || state == LexicalState.LINE_COMMENT)
                && delimiters.isEmpty();
        return new ScanResult(complete, unfinishedTemplate);
    }

    /** 扫描 CSS 字符串、块注释和花括号，任何提前闭合或结束时残留状态都视为明确截断。 */
    private ScanResult scanCss(String code) {
        Deque<Character> braces = new ArrayDeque<>();
        char quote = 0;
        boolean escaped = false;
        boolean comment = false;
        for (int i = 0; i < code.length(); i++) {
            char current = code.charAt(i);
            char next = i + 1 < code.length() ? code.charAt(i + 1) : 0;
            if (comment) {
                if (current == '*' && next == '/') {
                    comment = false;
                    i++;
                }
            } else if (quote != 0) {
                if (escaped) {
                    escaped = false;
                } else if (current == '\\') {
                    escaped = true;
                } else if (current == quote) {
                    quote = 0;
                } else if (current == '\n' || current == '\r') {
                    return ScanResult.incomplete(false);
                }
            } else if (current == '/' && next == '*') {
                comment = true;
                i++;
            } else if (current == '\'' || current == '"') {
                quote = current;
            } else if (current == '{') {
                braces.push(current);
            } else if (current == '}') {
                if (braces.isEmpty()) {
                    return ScanResult.incomplete(false);
                }
                braces.pop();
            }
        }
        return new ScanResult(braces.isEmpty() && quote == 0 && !comment, false);
    }

    /** 仅识别结尾运算符，以及扫描器已确认未闭合的模板插值，避免猜测一般 JavaScript 语义。 */
    private boolean isTrailingJavascriptFragment(String code, ScanResult scan) {
        String withoutTrailingComments = stripTrailingComments(code).trim();
        if (scan.unfinishedTemplate()) {
            return true;
        }
        if (withoutTrailingComments.endsWith("++") || withoutTrailingComments.endsWith("--")) {
            return false;
        }
        if (withoutTrailingComments.endsWith(".")) {
            int previous = withoutTrailingComments.length() - 2;
            return previous < 0 || !Character.isDigit(withoutTrailingComments.charAt(previous));
        }
        return TRAILING_OPERATOR.matcher(withoutTrailingComments).find();
    }

    /** 去除末尾注释，使运算符截断检查以最后一个实际代码字符为准。 */
    private String stripTrailingComments(String code) {
        String value = code;
        boolean changed;
        do {
            changed = false;
            String stripped = value.replaceFirst("(?s)/\\*.*?\\*/\\s*$", "");
            if (!stripped.equals(value)) {
                value = stripped;
                changed = true;
            }
            stripped = value.replaceFirst("(?m)//[^\\r\\n]*\\s*$", "");
            if (!stripped.equals(value)) {
                value = stripped;
                changed = true;
            }
        } while (changed);
        return value;
    }

    private int start(Pattern pattern, String value, int from) {
        Matcher matcher = pattern.matcher(value);
        return matcher.find(from) ? matcher.start() : -1;
    }

    private boolean matches(char open, char close) {
        return open == '(' && close == ')' || open == '[' && close == ']' || open == '{' && close == '}';
    }

    private boolean isExpressionPrefix(char value) {
        return "=(:,;!?&|+-*%^~<>".indexOf(value) >= 0;
    }

    private void addError(List<ArtifactValidationError> errors, String code, String message) {
        if (errors.stream().noneMatch(error -> error.code().equals(code))) {
            errors.add(new ArtifactValidationError(code, FILE, message));
        }
    }

    private enum LexicalState {
        CODE, SINGLE_QUOTE, DOUBLE_QUOTE, TEMPLATE, REGEX, LINE_COMMENT, BLOCK_COMMENT
    }

    private record Delimiter(char value, boolean templateExpression) { }

    private record ScanResult(boolean complete, boolean unfinishedTemplate) {
        private static ScanResult incomplete(boolean unfinishedTemplate) {
            return new ScanResult(false, unfinishedTemplate);
        }
    }
}
