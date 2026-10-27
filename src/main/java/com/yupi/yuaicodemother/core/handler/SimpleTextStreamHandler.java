package com.yupi.yuaicodemother.core.handler;

import com.yupi.yuaicodemother.enums.ChatHistoryMessageTypeEnum;
import com.yupi.yuaicodemother.ai.gateway.StaticGenerationStreamFrame;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 简单文本流处理器
 * 处理 HTML 和 MULTI_FILE 类型的流式响应
 */
@Slf4j
@Component
public class SimpleTextStreamHandler {

    /**
     * 处理静态流：Legacy 片段累加；LangGraph 展示与最终候选分离。
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
        // 每次订阅独立保存历史缓冲，避免重订阅把上一轮草稿或候选再次拼接。
        return Flux.defer(() -> {
            StringBuilder aiResponseBuilder = new StringBuilder();
            AtomicBoolean displaySeen = new AtomicBoolean();
            AtomicBoolean candidateSeen = new AtomicBoolean();
            return originFlux
                .<String>handle((chunk, sink) -> {
                    if (StaticGenerationStreamFrame.isDisplay(chunk)) {
                        displaySeen.set(true);
                        sink.next(StaticGenerationStreamFrame.content(chunk));
                    } else if (StaticGenerationStreamFrame.isCandidate(chunk)) {
                        candidateSeen.set(true);
                        aiResponseBuilder.setLength(0);
                        aiResponseBuilder.append(StaticGenerationStreamFrame.content(chunk));
                    } else {
                        // Legacy 与旧 Python 事件保持原有的文本累加语义。
                        aiResponseBuilder.append(chunk);
                        sink.next(chunk);
                    }
                })
                .doOnComplete(() -> {
                    if (displaySeen.get() && !candidateSeen.get()) {
                        throw new IllegalStateException("静态模型流缺少完整候选，不保存草稿历史");
                    }
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
        });
    }
}
