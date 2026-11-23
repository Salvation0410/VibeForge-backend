package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.util.StrUtil;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.enums.ChatHistoryMessageTypeEnum;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.model.entity.ChatHistoryOriginal;
import com.yupi.yuaicodemother.mapper.ChatHistoryOriginalMapper;
import com.yupi.yuaicodemother.service.ChatHistoryOriginalService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.util.List;
import java.util.stream.Collectors;

/**
 * 完整对话历史 服务层实现。
 *
 * @author song
 */
@Service
@Slf4j
public class ChatHistoryOriginalServiceImpl extends ServiceImpl<ChatHistoryOriginalMapper, ChatHistoryOriginal>  implements ChatHistoryOriginalService{
    /**
     * 添加对话历史
     * @param appId
     * @param message
     * @param messageType
     * @param userId
     * @return
     */
    @Override
    public boolean addOriginalChatMessage(Long appId, String message, String messageType, Long userId) {
        // 参数校验
        ThrowUtils.throwIf(appId == null || appId <= 0, ErrorCode.PARAMS_ERROR, "应用 ID不能为空");
        ThrowUtils.throwIf(StrUtil.isBlank(message), ErrorCode.PARAMS_ERROR, "消息内容不能为空");
        ThrowUtils.throwIf(StrUtil.isBlank(messageType), ErrorCode.PARAMS_ERROR, "消息类型不能为空");
        ThrowUtils.throwIf(userId == null || userId <= 0, ErrorCode.PARAMS_ERROR, "用户 ID不能为空");
        // 验证消息类型是否有效
        ChatHistoryMessageTypeEnum messageTypeEnum = ChatHistoryMessageTypeEnum.getEnumByValue(messageType);
        ThrowUtils.throwIf(messageTypeEnum == null, ErrorCode.SYSTEM_ERROR, "不支持的消息类型: " + messageType);
        // 对话消息入库
        ChatHistoryOriginal chatHistoryOriginal = ChatHistoryOriginal.builder()
                .appId(appId)
                .message(message)
                .messageType(messageType)
                .userId(userId)
                .build();
        return this.save(chatHistoryOriginal);
    }


    /**
     * 批量添加对话历史
     * @param chatHistoryOriginalList
     * @return
     */
    @Override
    public boolean addOriginalChatMessageBatch(List<ChatHistoryOriginal> chatHistoryOriginalList) {
        // 参数校验
        ThrowUtils.throwIf(chatHistoryOriginalList == null || chatHistoryOriginalList.isEmpty(),
                ErrorCode.PARAMS_ERROR, "消息列表不能为空");

        // 验证消息类型是否有效，无效类型的对话记录不进行入库
        List<ChatHistoryOriginal> validMessages = chatHistoryOriginalList.stream()
                .filter(chatHistory -> {
                    ChatHistoryMessageTypeEnum messageTypeEnum = ChatHistoryMessageTypeEnum.getEnumByValue(chatHistory.getMessageType());
                    if (messageTypeEnum == null) {
                        log.error("不支持的消息类型: {}", chatHistory.getMessageType());
                        return false; // 过滤掉无效消息
                    }
                    return true; // 保留有效消息
                })
                .collect(Collectors.toList());

        // 如果没有有效消息，直接返回
        if (validMessages.isEmpty()) {
            return false;
        }

        // 批量入库
        return this.saveBatch(validMessages);
    }


    /**
     * 根据应用 ID 删除对话历史
     * @param appId 应用 id
     * @return
     */
    @Override
    public boolean deleteByAppId(Long appId) {
        ThrowUtils.throwIf(appId == null || appId <= 0, ErrorCode.PARAMS_ERROR, "应用 ID不能为空");
        QueryWrapper queryWrapper = QueryWrapper.create()
                .eq("appId", appId);
        return this.remove(queryWrapper);
    }
}
