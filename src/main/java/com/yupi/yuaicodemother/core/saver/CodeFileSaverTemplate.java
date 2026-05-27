package com.yupi.yuaicodemother.core.saver;

import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import com.yupi.yuaicodemother.constant.AppConstant;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;

import java.io.File;
import java.nio.charset.StandardCharsets;

/**
 * @author huang
 * @version 1.0
 * @description
 * @date 2026/5/26
 */
public abstract class CodeFileSaverTemplate<T> {
    // 文件保存根目录
    protected static final String FILE_SAVE_ROOT_DIR = AppConstant.CODE_OUTPUT_ROOT_DIR;

    /*
    * 模板方法：保存代码的标准流程
    *
    * */
    public final File saveCode(T result, Long appId){
        validateInput(result);
        //构建唯一目录
        String baseDirPath = buildUniqueDir(appId);
        //保存文件(具体实现给子类)
        saveFiles(result, baseDirPath);
        //返回文件
        return new File(baseDirPath);
    }


    /**
     * 写入单个文件的工具方法
     *
     * @param dirPath  目录路径
     * @param filename 文件名
     * @param content  文件内容
     */
    protected final void writeToFile(String dirPath, String filename, String content) {
        if (StrUtil.isNotBlank(content)) {
            String filePath = dirPath + File.separator + filename;
            FileUtil.writeString(content, filePath, StandardCharsets.UTF_8);
        }
    }


    /**
     * 验证输入参数（可由子类覆盖）
     *
     * @param result 代码结果对象
     */
    protected void validateInput(T result) {
        if (result == null) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "代码结果对象不能为空");
        }
    }


    /**
     * 构建唯一目录路径：tmp/code_output/bizType_雪花ID
     *
     * @param appId 应用ID
     * @return 唯一目录路径
     */
    private String buildUniqueDir(Long appId) {
        if (appId == null) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "应用 ID 不能为空");
        }
        String codeType = getCodeType().getValue();
        String uniqueDirName = StrUtil.format("{}_{}", codeType, appId);
        String dirPath = FILE_SAVE_ROOT_DIR + File.separator + uniqueDirName;
        FileUtil.mkdir(dirPath);
        return dirPath;
    }

    /**
     * 获取代码类型（由子类实现）
     *
     * @return 代码生成类型
     */
    protected abstract CodeGenTypeEnum getCodeType();

    /**
     * 保存文件的具体实现（由子类实现）
     *
     * @param result      代码结果对象
     * @param baseDirPath 基础目录路径
     */
    protected abstract void saveFiles(T result, String baseDirPath);

}
