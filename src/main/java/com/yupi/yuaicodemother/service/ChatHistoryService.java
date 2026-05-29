package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.dto.chathistory.ChatHistoryQueryRequest;
import com.yupi.yuaicodemother.model.entity.ChatHistory;
import com.yupi.yuaicodemother.model.entity.SysUser;
import dev.langchain4j.memory.chat.MessageWindowChatMemory;

import java.time.LocalDateTime;

/**
 * 对话历史 服务层。
 *
 * @author song
 */
public interface ChatHistoryService extends IService<ChatHistory> {

    /**
     * 添加历史对话消息
     * @param appId 应用Id
     * @param message 消息
     * @param messageType 消息类型
     * @param userId 用户Id
     * @return 是否成功
     */
    public boolean addChatMessage(Long appId, String message, String messageType, Long userId);

    int loadChatHistoryToMemory(Long appId, MessageWindowChatMemory chatMemory, int maxCount);

    /**
     * 根据id删除关联历史记录
     * @param appId
     * @return
     */
    public boolean deleteByAppId(Long appId);


    /**
     * 获取查询条件
     * @param chatHistoryQueryRequest
     * @return
     */
    QueryWrapper getQueryWrapper(ChatHistoryQueryRequest chatHistoryQueryRequest);



    /**
     * 获取指定应用的历史对话
     * @param appId 应用Id
     * @param pageSize 每页大小
     * @param lastCreateTime 上次创建时间
     * @param loginUser 登录用户
     * @return
     */
    Page<ChatHistory> listAppChatHistoryByPage(Long appId, int pageSize,
                                               LocalDateTime lastCreateTime,
                                               SysUser loginUser);
}
