package com.yupi.yuaicodemother.core.saver;

import cn.hutool.core.util.StrUtil;
import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import lombok.extern.slf4j.Slf4j;

/**
 * 多文件代码保存模板
 */
@Slf4j
public class MultiFileCodeFileSaverTemplate extends CodeFileSaverTemplate<MultiFileCodeResult> {

    private static final String DEFAULT_CSS_CONTENT = "/* AI 未生成独立 CSS，保留占位文件以保证多文件结构完整 */";
    private static final String DEFAULT_JS_CONTENT = "// AI 未生成独立 JS，保留占位文件以保证多文件结构完整";

    @Override
    public CodeGenTypeEnum getCodeType() {
        return CodeGenTypeEnum.MULTI_FILE;
    }

    @Override
    protected void saveFiles(MultiFileCodeResult result, String baseDirPath) {
        writeToFile(baseDirPath, "index.html", result.getHtmlCode());

        String cssCode = result.getCssCode();
        if (StrUtil.isBlank(cssCode)) {
            cssCode = DEFAULT_CSS_CONTENT;
            log.warn("多文件模式未生成 CSS，已写入默认 style.css 占位文件");
        }
        writeToFile(baseDirPath, "style.css", cssCode);

        String jsCode = result.getJsCode();
        if (StrUtil.isBlank(jsCode)) {
            jsCode = DEFAULT_JS_CONTENT;
            log.warn("多文件模式未生成 JS，已写入默认 script.js 占位文件");
        }
        writeToFile(baseDirPath, "script.js", jsCode);
    }

    @Override
    protected void validateInput(MultiFileCodeResult result) {
        super.validateInput(result);
        if (StrUtil.isBlank(result.getHtmlCode())) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "HTML 代码内容不能为空");
        }
    }
}
