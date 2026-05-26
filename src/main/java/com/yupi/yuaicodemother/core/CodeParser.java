package com.yupi.yuaicodemother.core;

import com.yupi.yuaicodemother.ai.model.HtmlCodeResult;
import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * @author huang
 * @version 1.0
 * @description 代码解析器
 * @date 2026/5/26
 */
public class CodeParser {

    private static final Pattern HTML_CODE_PATTERN = Pattern.compile("```html\\s*\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);
    private static final Pattern CSS_CODE_PATTERN = Pattern.compile("```css\\s*\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);
    private static final Pattern JS_CODE_PATTERN = Pattern.compile("```(?:js|javascript)\\s*\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);

    // 兼容模型未使用标准 fenced code block、而是按“html 格式 / css 格式”分段输出的情况
    private static final Pattern HTML_SECTION_PATTERN = Pattern.compile(
            "html\\s*格式\\s*([\\s\\S]*?)(?=\\n\\s*css\\s*格式|\\n\\s*```(?:css|js|javascript)|\\z)",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern CSS_SECTION_PATTERN = Pattern.compile(
            "css\\s*格式\\s*([\\s\\S]*?)(?=\\n\\s*```(?:js|javascript)|\\z)",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern GENERIC_FENCE_PATTERN = Pattern.compile("```\\s*\\n?([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);

    /**
     * 解析 HTML 单文件代码
     */
    public static HtmlCodeResult parseHtmlCode(String codeContent) {
        HtmlCodeResult result = new HtmlCodeResult();
        String htmlCode = extractHtmlCode(codeContent);
        if (!isBlank(htmlCode)) {
            result.setHtmlCode(htmlCode.trim());
        } else {
            result.setHtmlCode(codeContent.trim());
        }
        return result;
    }

    /**
     * 解析多文件代码（HTML + CSS + JS）
     */
    public static MultiFileCodeResult parseMultiFileCode(String codeContent) {
        MultiFileCodeResult result = new MultiFileCodeResult();

        String htmlCode = extractCodeByPattern(codeContent, HTML_CODE_PATTERN);
        if (isBlank(htmlCode)) {
            htmlCode = extractCodeByPattern(codeContent, HTML_SECTION_PATTERN);
        }

        String cssCode = extractCodeByPattern(codeContent, CSS_CODE_PATTERN);
        if (isBlank(cssCode)) {
            cssCode = extractCodeByPattern(codeContent, CSS_SECTION_PATTERN);
        }

        String jsCode = extractCodeByPattern(codeContent, JS_CODE_PATTERN);
        if (isBlank(jsCode)) {
            jsCode = extractGenericFenceAfterSection(codeContent, "css");
        }

        if (!isBlank(htmlCode)) {
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

    /**
     * 提取 HTML 代码内容
     */
    private static String extractHtmlCode(String content) {
        Matcher matcher = HTML_CODE_PATTERN.matcher(content);
        if (matcher.find()) {
            return matcher.group(1);
        }
        return null;
    }

    /**
     * 根据正则模式提取代码
     */
    private static String extractCodeByPattern(String content, Pattern pattern) {
        Matcher matcher = pattern.matcher(content);
        if (matcher.find()) {
            return matcher.group(1);
        }
        return null;
    }

    /**
     * 兼容未标注语言的普通代码块。
     */
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

    private static boolean isBlank(String content) {
        return content == null || content.trim().isEmpty();
    }
}
