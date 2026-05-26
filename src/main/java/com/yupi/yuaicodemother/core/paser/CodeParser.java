package com.yupi.yuaicodemother.core.paser;

/**
 * @author huang
 * @version 1.0
 * @description 代码解析器策略接口
 * @date 2026/5/26
 */
public interface CodeParser<T> {

    /**
     * 解析代码
     *
     * @param codeContent 代码内容
     * @return 解析结果
     */
    T parseCode(String codeContent);
}
