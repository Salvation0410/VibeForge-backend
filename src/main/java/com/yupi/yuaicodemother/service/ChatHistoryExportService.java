package com.yupi.yuaicodemother.service;

import com.yupi.yuaicodemother.model.entity.SysUser;

/**
 * 对话历史导出服务
 */
public interface ChatHistoryExportService {

    /**
     * 导出指定应用的 Markdown 对话记录
     *
     * @param appId 应用 id
     * @param loginUser 当前登录用户
     * @return Markdown 文本
     */
    String exportAppChatHistoryAsMarkdown(Long appId, SysUser loginUser);

    /**
     * 生成导出文件名
     *
     * @param appId 应用 id
     * @return 文件名
     */
    String buildMarkdownFileName(Long appId);
}

