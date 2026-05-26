package com.yupi.yuaicodemother.core.paser;

import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * @author huang
 * @version 1.0
 * @description
 * @date 2026/5/26
 */
public class MultiFileCodeParser implements CodeParser<MultiFileCodeResult>{

    private static final Pattern HTML_CODE_PATTERN = Pattern.compile("```html\\s*\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);
    private static final Pattern CSS_CODE_PATTERN = Pattern.compile("```css\\s*\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);
    private static final Pattern JS_CODE_PATTERN = Pattern.compile("```(?:js|javascript)\\s*\\n([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);

    private static final Pattern HTML_SECTION_PATTERN = Pattern.compile(
            "html\\s*格式\\s*([\\s\\S]*?)(?=\\n\\s*css\\s*格式|\\n\\s*```(?:css|js|javascript)|\\z)",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern CSS_SECTION_PATTERN = Pattern.compile(
            "css\\s*格式\\s*([\\s\\S]*?)(?=\\n\\s*```(?:js|javascript)|\\z)",
            Pattern.CASE_INSENSITIVE
    );
    private static final Pattern GENERIC_FENCE_PATTERN = Pattern.compile("```\\s*\\n?([\\s\\S]*?)```", Pattern.CASE_INSENSITIVE);

    /*
    * 解析多文件
    * */
    @Override
    public MultiFileCodeResult parseCode(String codeContent) {
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
