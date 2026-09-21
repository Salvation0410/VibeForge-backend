package com.yupi.yuaicodemother.core.paser;

import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;
import com.yupi.yuaicodemother.core.artifact.ArtifactValidationException;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 按固定的三文件 Markdown 协议解析候选产物，不猜测缺失区块。
 */
public class MultiFileCodeParser implements CodeParser<MultiFileCodeResult> {
    private static final Pattern THREE_FILES = Pattern.compile(
            "\\A\\s*index\\.html\\R```html\\R([\\s\\S]*?)\\R```\\R\\s*"
                    + "style\\.css\\R```css\\R([\\s\\S]*?)\\R```\\R\\s*"
                    + "script\\.js\\R```(?:javascript|js)\\R([\\s\\S]*?)\\R```\\s*\\z",
            Pattern.CASE_INSENSITIVE);

    /**
     * 解析完整的 HTML、CSS、JavaScript 区块；任何缺失、重复或截断都拒绝整个响应。
     *
     * @param codeContent 模型返回的完整文本
     * @return 三个完整文件的内容
     * @throws ArtifactValidationException 响应未遵循三文件协议时抛出
     */
    @Override
    public MultiFileCodeResult parseCode(String codeContent) {
        String content = codeContent == null ? "" : codeContent;
        Matcher matcher = THREE_FILES.matcher(content);
        if (countFences(content) != 6 || !matcher.matches() || matcher.group(1).isBlank() || matcher.group(2).isBlank()
                || matcher.group(3).isBlank()) {
            throw new ArtifactValidationException("MULTI_FILE_FORMAT_INVALID", null,
                    "多文件响应必须包含完整且唯一的 index.html、style.css 和 script.js 区块");
        }
        MultiFileCodeResult result = new MultiFileCodeResult();
        result.setHtmlCode(matcher.group(1).trim());
        result.setCssCode(matcher.group(2).trim());
        result.setJsCode(matcher.group(3).trim());
        return result;
    }

    /**
     * 统计 Markdown 围栏标记，确保响应只包含三个成对代码块。
     *
     * @param content 待解析的完整响应
     * @return 三反引号标记出现次数
     */
    private int countFences(String content) {
        int count = 0;
        int index = 0;
        while ((index = content.indexOf("```", index)) >= 0) {
            count++;
            index += 3;
        }
        return count;
    }
}
