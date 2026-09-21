package com.yupi.yuaicodemother.core.handler;

import com.yupi.yuaicodemother.enums.ChatHistoryMessageTypeEnum;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

/**
 * 简单文本流处理器
 * 处理 HTML 和 MULTI_FILE 类型的流式响应
 */
@Slf4j
@Component
public class SimpleTextStreamHandler {

    /**
     * 处理传统流（HTML, MULTI_FILE）
     * 直接收集完整的文本响应
     *
     * @param originFlux         原始流
     * @param chatHistoryService 聊天历史服务
     * @param appId              应用ID
     * @param loginUser          登录用户
     * @return 处理后的流
     */
    public Flux<String> handle(Flux<String> originFlux,
                               ChatHistoryService chatHistoryService,
                               long appId, SysUser loginUser) {
        StringBuilder aiResponseBuilder = new StringBuilder();
        return originFlux
                .map(chunk -> {
                    // 收集AI响应内容
                    aiResponseBuilder.append(chunk);
                    return chunk;
                })
                .doOnComplete(() -> {
                    try {
                        // 产物已经成功发布，历史落库失败只能记录，不能把成功终态翻转为 error。
                        String aiResponse = aiResponseBuilder.toString();
                        chatHistoryService.addChatMessage(appId, aiResponse,
                                ChatHistoryMessageTypeEnum.AI.getValue(), loginUser.getId());
                    } catch (Exception historyError) {
                        log.error("已发布 AI 回复写入聊天历史失败, appId={}", appId, historyError);
                    }
                })
                .doOnError(error -> {
                    // 失败候选不能进入成功对话历史，否则后续模型会把截断代码当作有效上下文。
                    log.warn("AI 回复未发布，不写入对话历史, appId={}", appId, error);
                });
    }
}
