package com.yupi.yuaicodemother.core;

import com.yupi.yuaicodemother.ai.model.HtmlCodeResult;
import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;
import com.yupi.yuaicodemother.core.paser.HtmlCodeParser;
import com.yupi.yuaicodemother.core.paser.MultiFileCodeParser;

/**
 * 旧版代码解析入口，内部代理到新的解析器实现
 */
@Deprecated
public class CodeParser {

    private static final HtmlCodeParser HTML_CODE_PARSER = new HtmlCodeParser();
    private static final MultiFileCodeParser MULTI_FILE_CODE_PARSER = new MultiFileCodeParser();

    public static HtmlCodeResult parseHtmlCode(String codeContent) {
        return HTML_CODE_PARSER.parseCode(codeContent);
    }

    public static MultiFileCodeResult parseMultiFileCode(String codeContent) {
        return MULTI_FILE_CODE_PARSER.parseCode(codeContent);
    }
}
