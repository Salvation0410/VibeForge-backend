package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.entity.ChatHistoryOriginal;

import java.util.List;

/**
 * 完整对话历史 服务层
 *
 * 保存 Vue 工程生成的原始消息与工具调用记录
 *
 * @author song
 */

public interface ChatHistoryOriginalService extends IService<ChatHistoryOriginal> {

    /**
     * 添加对话历史
     * @param appId
     * @param message
     * @param messageType
     * @param userId
     * @return
     */
    boolean addOriginalChatMessage(Long appId, String message, String messageType, Long userId);

    /**
     * 批量添加对话历史
     * @param chatHistoryOriginalList
     * @return
     */
    boolean addOriginalChatMessageBatch(List<ChatHistoryOriginal> chatHistoryOriginalList);

    /**
     * 根据 appId 关联删除对话历史记录
     * @param appId
     * @return
     */
    boolean deleteByAppId(Long appId);

}
