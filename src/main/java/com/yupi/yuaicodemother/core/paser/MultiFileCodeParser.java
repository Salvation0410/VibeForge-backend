package com.yupi.yuaicodemother.core.paser;

import cn.hutool.core.util.StrUtil;
import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;

import java.util.LinkedHashMap;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 多文件代码解析器
 */
public class MultiFileCodeParser implements CodeParser<MultiFileCodeResult> {

    private static final Pattern HTML_CODE_PATTERN = Pattern.compile("```html\\s*\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);
    private static final Pattern CSS_CODE_PATTERN = Pattern.compile("```css\\s*\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);
    private static final Pattern JS_CODE_PATTERN = Pattern.compile("```(?:js|javascript)\\s*\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);
    private static final Pattern CODE_FENCE_PATTERN = Pattern.compile("```([^\\r\\n`]*)\\r?\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);
    private static final Pattern FILE_NAME_LABEL_PATTERN = Pattern.compile(
            "(?:^|\\n)\\s*(index\\.html|style\\.css|script\\.js)\\s*\\n\\s*```([^\\r\\n`]*)\\r?\\n([\\s\\S]*?)```",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern HTML_SECTION_PATTERN = Pattern.compile(
            "html\\s*格式\\s*([\\s\\S]*?)(?=\\n\\s*css\\s*格式|\\n\\s*```(?:css|js|javascript)|\\z)",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern CSS_SECTION_PATTERN = Pattern.compile(
            "css\\s*格式\\s*([\\s\\S]*?)(?=\\n\\s*(?:js|javascript)\\s*格式|\\n\\s*```(?:js|javascript)|\\z)",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern JS_SECTION_PATTERN = Pattern.compile(
            "js(?:avascript)?\\s*格式\\s*([\\s\\S]*?)\\z",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern GENERIC_FENCE_PATTERN = Pattern.compile("```\\s*\\n?([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);
    private static final Pattern HTML_STYLE_PATTERN = Pattern.compile("<style[^>]*>([\\s\\S]*?)</style>", Pattern.CASE_INSENSITIVE);
    private static final Pattern HTML_SCRIPT_PATTERN = Pattern.compile("<script(?![^>]*\\bsrc=)[^>]*>([\\s\\S]*?)</script>", Pattern.CASE_INSENSITIVE);

    @Override
    public MultiFileCodeResult parseCode(String codeContent) {
        MultiFileCodeResult result = new MultiFileCodeResult();
        Map<String, String> codeByType = extractCodeBlocks(codeContent);

        String htmlCode = firstNonBlank(
                codeByType.get("html"),
                extractCodeByPattern(codeContent, HTML_CODE_PATTERN),
                extractCodeByPattern(codeContent, HTML_SECTION_PATTERN),
                extractGenericFenceByContentType(codeContent, "html")
        );
        String cssCode = firstNonBlank(
                codeByType.get("css"),
                extractCodeByPattern(codeContent, CSS_CODE_PATTERN),
                extractCodeByPattern(codeContent, CSS_SECTION_PATTERN),
                extractGenericFenceAfterSection(codeContent, "css"),
                extractGenericFenceAfterSection(codeContent, "style.css"),
                extractGenericFenceByContentType(codeContent, "css")
        );
        String jsCode = firstNonBlank(
                codeByType.get("js"),
                extractCodeByPattern(codeContent, JS_CODE_PATTERN),
                extractCodeByPattern(codeContent, JS_SECTION_PATTERN),
                extractGenericFenceAfterSection(codeContent, "js"),
                extractGenericFenceAfterSection(codeContent, "javascript"),
                extractGenericFenceAfterSection(codeContent, "script.js"),
                extractGenericFenceByContentType(codeContent, "js")
        );

        if (!isBlank(htmlCode)) {
            if (isBlank(cssCode)) {
                cssCode = extractInlineStyle(htmlCode);
            }
            if (isBlank(jsCode)) {
                jsCode = extractInlineScript(htmlCode);
            }
            htmlCode = normalizeHtmlReferences(htmlCode, cssCode, jsCode);
            result.setHtmlCode(htmlCode.trim());
        }
        if (!isBlank(cssCode)) {
            result.setCssCode(cssCode.trim());
        }
        if (!isBlank(jsCode)) {
            result.setJsCode(jsCode.trim());
        }
        return result;
    }

    private static Map<String, String> extractCodeBlocks(String content) {
        Map<String, String> codeByType = new LinkedHashMap<>();
        Matcher fileNameMatcher = FILE_NAME_LABEL_PATTERN.matcher(content);
        while (fileNameMatcher.find()) {
            putByFileName(codeByType, fileNameMatcher.group(1), fileNameMatcher.group(3));
        }

        Matcher matcher = CODE_FENCE_PATTERN.matcher(content);
        while (matcher.find()) {
            putByFenceInfo(codeByType, matcher.group(1), matcher.group(2));
        }
        return codeByType;
    }

    private static void putByFileName(Map<String, String> codeByType, String fileName, String code) {
        if (isBlank(fileName) || isBlank(code)) {
            return;
        }
        String normalized = fileName.trim().toLowerCase();
        if ("index.html".equals(normalized)) {
            codeByType.putIfAbsent("html", code);
        } else if ("style.css".equals(normalized)) {
            codeByType.putIfAbsent("css", code);
        } else if ("script.js".equals(normalized)) {
            codeByType.putIfAbsent("js", code);
        }
    }

    private static void putByFenceInfo(Map<String, String> codeByType, String info, String code) {
        if (isBlank(code)) {
            return;
        }
        String normalizedInfo = StrUtil.blankToDefault(info, "").trim().toLowerCase();
        if (normalizedInfo.contains("index.html") || normalizedInfo.contains("html")) {
            codeByType.putIfAbsent("html", code);
            return;
        }
        if (normalizedInfo.contains("style.css") || normalizedInfo.contains("css")) {
            codeByType.putIfAbsent("css", code);
            return;
        }
        if (normalizedInfo.contains("script.js") || normalizedInfo.contains("javascript") || "js".equals(normalizedInfo)) {
            codeByType.putIfAbsent("js", code);
        }
    }

    private static String extractCodeByPattern(String content, Pattern pattern) {
        Matcher matcher = pattern.matcher(content);
        if (matcher.find()) {
            return matcher.group(1);
        }
        return null;
    }

    private static String extractGenericFenceAfterSection(String content, String sectionName) {
        String lowerContent = content.toLowerCase();
        int sectionIndex = lowerContent.indexOf(sectionName.toLowerCase());
        if (sectionIndex < 0) {
            return null;
        }
        String sectionContent = content.substring(sectionIndex);
        Matcher matcher = GENERIC_FENCE_PATTERN.matcher(sectionContent);
        if (matcher.find()) {
            return matcher.group(1);
        }
        return null;
    }

    private static String extractGenericFenceByContentType(String content, String expectedType) {
        Matcher matcher = CODE_FENCE_PATTERN.matcher(content);
        while (matcher.find()) {
            String code = matcher.group(2);
            if (matchesContentType(code, expectedType)) {
                return code;
            }
        }
        return null;
    }

    private static boolean matchesContentType(String code, String expectedType) {
        if (isBlank(code)) {
            return false;
        }
        String trimmedCode = code.trim().toLowerCase();
        return switch (expectedType) {
            case "html" -> trimmedCode.startsWith("<!doctype html")
                    || trimmedCode.startsWith("<html")
                    || trimmedCode.contains("<body");
            case "css" -> trimmedCode.contains("{") && !trimmedCode.contains("<");
            case "js" -> trimmedCode.contains("function")
                    || trimmedCode.contains("const ")
                    || trimmedCode.contains("let ")
                    || trimmedCode.contains("var ")
                    || trimmedCode.contains("document.")
                    || trimmedCode.contains("window.")
                    || trimmedCode.contains("addEventListener");
            default -> false;
        };
    }

    private static String extractInlineStyle(String htmlCode) {
        Matcher matcher = HTML_STYLE_PATTERN.matcher(htmlCode);
        if (matcher.find()) {
            return matcher.group(1);
        }
        return null;
    }

    private static String extractInlineScript(String htmlCode) {
        Matcher matcher = HTML_SCRIPT_PATTERN.matcher(htmlCode);
        if (matcher.find()) {
            return matcher.group(1);
        }
        return null;
    }

    private static String normalizeHtmlReferences(String htmlCode, String cssCode, String jsCode) {
        String normalizedHtml = htmlCode;
        if (!isBlank(cssCode)) {
            normalizedHtml = HTML_STYLE_PATTERN.matcher(normalizedHtml).replaceFirst("");
            if (!normalizedHtml.toLowerCase().contains("style.css")) {
                normalizedHtml = normalizedHtml.replaceFirst("(?i)</head>", "    <link rel=\"stylesheet\" href=\"style.css\">\\n</head>");
            }
        }
        if (!isBlank(jsCode)) {
            normalizedHtml = HTML_SCRIPT_PATTERN.matcher(normalizedHtml).replaceFirst("");
            if (!normalizedHtml.toLowerCase().contains("script.js")) {
                normalizedHtml = normalizedHtml.replaceFirst("(?i)</body>", "    <script src=\"script.js\"></script>\\n</body>");
            }
        }
        return normalizedHtml;
    }

    private static String firstNonBlank(String... values) {
        for (String value : values) {
            if (!isBlank(value)) {
                return value;
            }
        }
        return null;
    }

    private static boolean isBlank(String content) {
        return content == null || content.trim().isEmpty();
    }
}
