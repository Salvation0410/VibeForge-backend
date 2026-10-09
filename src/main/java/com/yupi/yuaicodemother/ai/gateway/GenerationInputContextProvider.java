package com.yupi.yuaicodemother.ai.gateway;

import com.mybatisflex.core.query.QueryWrapper;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.mapper.AppMapper;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;

import java.nio.file.Files;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Objects;

/** 由业务所有者提供有界审核上下文，不让 Python 查询业务数据库或读取项目目录。 */
@Component
@RequiredArgsConstructor
public class GenerationInputContextProvider {
    private final AppMapper appMapper;
    private final ChatHistoryService chatHistoryService;
    private final ArtifactPathResolver artifactPathResolver;

    public Map<String, Object> build(Long appId, Long userId, String prompt, CodeGenTypeEnum type) {
        var app = appMapper.selectOneById(appId);
        if (app == null || !Objects.equals(app.getUserId(), userId)) {
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "无法读取当前应用的审核上下文");
        }
        // 仅传用户需求，避免完整源码、工具日志或错误文本挤占审核窗口。
        var rows = chatHistoryService.list(QueryWrapper.create()
                .eq("appId", appId).eq("userId", userId).eq("messageType", "user")
                .orderBy("id", false).limit(7));
        List<Map<String, String>> history = new ArrayList<>();
        boolean truncated = rows.size() >= 7;
        boolean skippedCurrent = false;
        for (var row : rows) {
            String message = Objects.toString(row.getMessage(), "");
            // 当前消息已在业务入口落库，跳过一次，不能重复充当上一轮上下文。
            if (!skippedCurrent && message.equals(prompt)) {
                skippedCurrent = true;
                continue;
            }
            if (history.size() == 6) break;
            truncated |= message.length() > 1000;
            history.add(Map.of("role", "user", "content", truncate(message, 1000)));
        }
        Collections.reverse(history);
        String initial = Objects.toString(app.getInitPrompt(), "");
        return Map.of("conversation", history, "metadata", Map.of(
                "userId", String.valueOf(userId), "existingProject",
                Files.isDirectory(artifactPathResolver.resolveActiveRoot(type, appId)),
                "initialPrompt", truncate(initial, 2000),
                "initialPromptTruncated", initial.length() > 2000, "historyTruncated", truncated));
    }

    static String truncate(String text, int maxLength) {
        int end = Math.min(maxLength, text.length());
        // UTF-16 截断边界不能拆开表情等代理对，否则 Python 收到的历史无法编码为 UTF-8。
        if (end > 0 && end < text.length() && Character.isHighSurrogate(text.charAt(end - 1))) end--;
        return text.substring(0, end);
    }
}
