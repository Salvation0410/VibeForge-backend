package com.yupi.yuaicodemother.core.paser;

import com.yupi.yuaicodemother.core.artifact.HtmlArtifactParser;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;

/**
 * @author huang
 * @version 1.0
 * @description 代码解析执行器 根据代码类型执行对应的解析逻辑
 * @date 2026/5/26
 */
public class CodeParserExecutor {
    private static final HtmlArtifactParser htmlArtifactParser = new HtmlArtifactParser();

    private static final MultiFileCodeParser multiFileCodeParser = new MultiFileCodeParser();

    /**
     * 执行代码解析
     *
     * @param codeContent 代码内容
     * @param codeGenType 代码生成类型
     * @return 解析结果（HtmlCodeResult 或 MultiFileCodeResult）
     * @throws com.yupi.yuaicodemother.core.artifact.ArtifactValidationException 候选产物不满足对应类型的完整格式时抛出，不返回部分结果
     */
    public static Object executeParser(String codeContent, CodeGenTypeEnum codeGenType) {
        return switch (codeGenType) {
            case HTML -> htmlArtifactParser.parse(codeContent);
            case MULTI_FILE -> multiFileCodeParser.parseCode(codeContent);
            default -> throw new BusinessException(ErrorCode.SYSTEM_ERROR, "不支持的代码生成类型: " + codeGenType);
        };
    }
}
