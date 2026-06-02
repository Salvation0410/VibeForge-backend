package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.entity.ChatHistoryOriginal;
import dev.langchain4j.memory.chat.MessageWindowChatMemory;

import java.util.List;

/**
 * 完整对话历史 服务层
 *
 * 为vue工程模式恢复对话记忆(包含工具调用信息)
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

    /**
     * 将 APP 的对话历史加载到缓存中
     * @param appId
     * @param chatMemory
     * @param maxCount
     * @return
     */
    int loadOriginalChatHistoryToMemory(Long appId, MessageWindowChatMemory chatMemory, int maxCount);
}
