package com.yupi.yuaicodemother.core.handler;

import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONObject;
import cn.hutool.json.JSONUtil;
import com.yupi.yuaicodemother.ai.model.message.*;
import com.yupi.yuaicodemother.enums.ChatHistoryMessageTypeEnum;
import com.yupi.yuaicodemother.model.entity.ChatHistoryOriginal;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.ChatHistoryOriginalService;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

import java.util.ArrayList;
import java.util.List;
import java.util.HashSet;
import java.util.Set;

/**
 * JSON 消息流处理器
 * 处理 VUE_PROJECT 类型的复杂流式响应，包含工具调用信息
 */
@Slf4j
@Component
public class JsonMessageStreamHandler {

    /**
     * 处理 Python 工具事件流（VUE_PROJECT）
     * 解析 JSON 消息并重组为完整的响应格式
     *
     * @param originFlux         原始流
     * @param chatHistoryService 聊天历史服务
     * @param appId              应用ID
     * @param loginUser          登录用户
     * @return 处理后的流
     */
    public Flux<String> handle(Flux<String> originFlux,
                               ChatHistoryService chatHistoryService,
                               ChatHistoryOriginalService chatHistoryOriginalService,
                               long appId, SysUser loginUser) {
        // 收集数据用于前端展示(对话历史)
        StringBuilder chatHistoryStringBuilder = new StringBuilder();
        // 收集用于保存原始聊天记录的文本
        StringBuilder aiResponseStringBuilder = new StringBuilder();
        //每个 Flux 流可能包含多条工具调用和 AI_RESPONSE 响应信息，统一收集之后批量入库
        List<ChatHistoryOriginal> originalChatHistoryList = new ArrayList<>();
        // 同一请求内的重复开始事件只提示一次，不写入最终展示历史。
        Set<String> seenToolIds = new HashSet<>();
        return originFlux
                .map(chunk -> {
                    // 解析每个 JSON 消息块
                    return handleJsonMessageChunk(chunk, chatHistoryStringBuilder,aiResponseStringBuilder, originalChatHistoryList, seenToolIds);
                })
                .filter(StrUtil::isNotEmpty) // 过滤空字串
                .doOnComplete(() -> {
                    if(!originalChatHistoryList.isEmpty()){
                        // 完善 ChatHistoryOriginal 信息
                        originalChatHistoryList.forEach(chatHistory -> {
                            chatHistory.setAppId(appId);
                            chatHistory.setUserId(loginUser.getId());
                        });
                        // 批量入库
                        chatHistoryOriginalService.addOriginalChatMessageBatch(originalChatHistoryList);
                    }
                    // AI 响应入库（两种情况：1. 未进行工具调用；2. 工具调用后 AI 返回结果）。
                    String aiResponseStr = aiResponseStringBuilder.toString();
                    if (StrUtil.isNotBlank(aiResponseStr)) {
                        chatHistoryOriginalService.addOriginalChatMessage(appId, aiResponseStr, ChatHistoryMessageTypeEnum.AI.getValue(), loginUser.getId());
                    }

                    // 流式响应完成后，添加 AI 消息到对话历史
                    String aiResponse = chatHistoryStringBuilder.toString();
                    chatHistoryService.addChatMessage(appId, aiResponse, ChatHistoryMessageTypeEnum.AI.getValue(), loginUser.getId());
                })
                .doOnError(error -> {
                    // 如果AI回复失败，也要记录错误消息
                    String errorMessage = "AI回复失败: " + error.getMessage();
                    chatHistoryService.addChatMessage(appId, errorMessage, ChatHistoryMessageTypeEnum.AI.getValue(), loginUser.getId());
                    chatHistoryOriginalService.addOriginalChatMessage(appId, errorMessage, ChatHistoryMessageTypeEnum.AI.getValue(), loginUser.getId());
                });
    }

    /**
     * 解析并收集 Python 工具事件
     */
    private String handleJsonMessageChunk(String chunk,
                                          StringBuilder chatHistoryStringBuilder,
                                          StringBuilder aiResponseStringBuilder,
                                          List<ChatHistoryOriginal> originalChatHistoryList,
                                          Set<String> seenToolIds) {
        StreamMessage streamMessage = JSONUtil.toBean(chunk, StreamMessage.class);
        StreamMessageTypeEnum typeEnum = StreamMessageTypeEnum.getEnumByValue(streamMessage.getType());
        switch (typeEnum) {
            case AI_RESPONSE -> {
                String data = JSONUtil.toBean(chunk, AiResponseMessage.class).getData();
                chatHistoryStringBuilder.append(data);
                aiResponseStringBuilder.append(data);
                // 仅返回当前分片，避免客户端重复拼接历史文本。
                return data;
            }
            case TOOL_REQUEST -> {
                ToolRequestMessage request = JSONUtil.toBean(chunk, ToolRequestMessage.class);
                if (request.getId() == null || !seenToolIds.add(request.getId())) return "";
                String label = switch (request.getName() == null ? "" : request.getName()) {
                    case "file_write" -> "写入文件";
                    case "file_read" -> "读取文件";
                    case "dir_read" -> "读取目录";
                    case "file_modify" -> "修改文件";
                    case "file_delete" -> "删除文件";
                    default -> "";
                };
                // 保留文件工具的开始提示；图片搜索与工作流内部工具继续隐藏开始事件。
                return label.isEmpty() ? "" : "\n\n[选择工具] " + label + "\n\n";
            }
            case TOOL_EXECUTED -> {
                processToolExecutionMessage(aiResponseStringBuilder, chunk, originalChatHistoryList);
                JSONObject envelope = JSONUtil.parseObj(chunk);
                JSONObject args = JSONUtil.parseObj(envelope.getStr("arguments", "{}"));
                JSONObject toolResult = JSONUtil.parseObj(envelope.getStr("result", "{}"));
                String label = switch (envelope.getStr("name", "")) {
                    case "file_write" -> "写入文件";
                    case "file_read" -> "读取文件";
                    case "dir_read" -> "读取目录";
                    case "file_modify" -> "修改文件";
                    case "file_delete" -> "删除文件";
                    case "image_search" -> "搜索图片";
                    default -> "";
                };
                // 工作流内部校验、构建和发布事件留存原始记录，不暴露为文件工具展示。
                if (label.isEmpty()) return "";
                String path = args.getStr("relativeFilePath",
                        args.getStr("relativeDirPath", args.getStr("query", "")));
                String status = Boolean.FALSE.equals(toolResult.getBool("ok")) ? "未完成" : "完成";
                String output = "\n\n[工具调用] " + label + " " + path + "（" + status + "）\n\n";
                chatHistoryStringBuilder.append(output);
                return output;
            }
            default -> {
                log.error("不支持的消息类型: {}", typeEnum);
                return "";
            }
        }
    }

    /**
     * 解析处理工具的调用信息
     * @param aiResponseStringBuilder
     * @param chunk
     * @param originalChatHistoryList
     */
    private void processToolExecutionMessage(StringBuilder aiResponseStringBuilder, String chunk, List<ChatHistoryOriginal> originalChatHistoryList) {
        // 解析 chunk
        ToolExecutedMessage toolExecutedMessage = JSONUtil.toBean(chunk, ToolExecutedMessage.class);
        // 结果事件携带调用参数，用它恢复配对请求记录并保留之前的 AI 文本。
        String aiResponseStr = aiResponseStringBuilder.toString();
        ToolRequestMessage toolRequestMessage = new ToolRequestMessage();
        toolRequestMessage.setId(toolExecutedMessage.getId());
        toolRequestMessage.setName(toolExecutedMessage.getName());
        toolRequestMessage.setArguments(toolExecutedMessage.getArguments());
        toolRequestMessage.setText(aiResponseStr);
        // 转换成 JSON
        String toolRequestJsonStr = JSONUtil.toJsonStr(toolRequestMessage);
        // 构造 ChatHistory 存入列表
        ChatHistoryOriginal toolRequestHistory = ChatHistoryOriginal.builder()
                .message(toolRequestJsonStr)
                .messageType(ChatHistoryMessageTypeEnum.TOOL_EXECUTION_REQUEST.getValue())
                .build();
        originalChatHistoryList.add(toolRequestHistory);
        ChatHistoryOriginal toolResultHistory = ChatHistoryOriginal.builder()
                .message(chunk)
                .messageType(ChatHistoryMessageTypeEnum.TOOL_EXECUTION_RESULT.getValue())
                .build();
        originalChatHistoryList.add(toolResultHistory);
        // AI 响应内容暂时结束，置空 aiResponseStringBuilder
        aiResponseStringBuilder.setLength(0);
    }

}
