package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.StrUtil;
import com.mybatisflex.core.query.QueryWrapper;
import com.yupi.yuaicodemother.constant.AppConstant;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.enums.ChatHistoryMessageTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.ChatHistory;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.AppService;
import com.yupi.yuaicodemother.service.ChatHistoryExportService;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * 对话历史导出服务实现
 */
@Service
@RequiredArgsConstructor
public class ChatHistoryExportServiceImpl implements ChatHistoryExportService {

    private static final Set<String> TEXT_FILE_EXTENSIONS = Set.of(
            "html", "htm", "css", "js", "jsx", "ts", "tsx", "vue",
            "json", "md", "markdown", "yaml", "yml", "txt", "xml"
    );

    private final AppService appService;
    private final ChatHistoryService chatHistoryService;

    @Override
    public String exportAppChatHistoryAsMarkdown(Long appId, SysUser loginUser) {
        ThrowUtils.throwIf(appId == null || appId <= 0, ErrorCode.PARAMS_ERROR, "应用 ID 不能为空");
        ThrowUtils.throwIf(loginUser == null, ErrorCode.NOT_LOGIN_ERROR);

        App app = appService.getById(appId);
        ThrowUtils.throwIf(app == null, ErrorCode.NOT_FOUND_ERROR, "应用不存在");

        boolean isAdmin = UserConstant.ADMIN_ROLE.equals(loginUser.getUserRole());
        boolean isCreator = app.getUserId() != null && app.getUserId().equals(loginUser.getId());
        ThrowUtils.throwIf(!isAdmin && !isCreator, ErrorCode.NO_AUTH_ERROR, "无权导出该应用的对话记录");

        List<ChatHistory> chatHistories = loadSortedChatHistories(appId);
        ThrowUtils.throwIf(chatHistories.isEmpty(), ErrorCode.NOT_FOUND_ERROR, "当前应用暂无可导出的对话记录");

        File sourceDir = resolveSourceDir(app);
        List<File> sourceFiles = listExportableSourceFiles(sourceDir);
        ThrowUtils.throwIf(sourceFiles.isEmpty(), ErrorCode.OPERATION_ERROR, "请先完成一次代码生成后再导出");

        return buildMarkdown(app, chatHistories, sourceDir, sourceFiles);
    }

    @Override
    public String buildMarkdownFileName(Long appId) {
        App app = appService.getById(appId);
        String appName = app != null ? app.getAppName() : null;
        String safeName = sanitizeFileName(StrUtil.blankToDefault(appName, "应用"));
        return safeName + "-开发对话记录.md";
    }

    private List<ChatHistory> loadSortedChatHistories(Long appId) {
        QueryWrapper queryWrapper = QueryWrapper.create()
                .eq("appId", appId)
                .orderBy("createTime", true)
                .orderBy("id", true);
        return chatHistoryService.list(queryWrapper);
    }

    private File resolveSourceDir(App app) {
        String codeGenType = StrUtil.blankToDefault(app.getCodeGenType(), "multi_file");
        String sourceDirName = codeGenType + "_" + app.getId();
        File sourceDir = new File(AppConstant.CODE_OUTPUT_ROOT_DIR, sourceDirName);
        if (!sourceDir.exists() || !sourceDir.isDirectory()) {
            throw new BusinessException(ErrorCode.OPERATION_ERROR, "请先完成一次代码生成后再导出");
        }
        return sourceDir;
    }

    private List<File> listExportableSourceFiles(File sourceDir) {
        return FileUtil.loopFiles(sourceDir)
                .stream()
                .filter(File::isFile)
                .filter(this::isExportableTextFile)
                .sorted(Comparator.comparing(file -> normalizePath(FileUtil.subPath(sourceDir.getAbsolutePath(), file.getAbsolutePath()))))
                .toList();
    }

    private boolean isExportableTextFile(File file) {
        String extension = FileUtil.extName(file).toLowerCase(Locale.ROOT);
        return TEXT_FILE_EXTENSIONS.contains(extension);
    }

    private String buildMarkdown(App app, List<ChatHistory> chatHistories, File sourceDir, List<File> sourceFiles) {
        StringBuilder markdown = new StringBuilder();
        markdown.append("# ").append(StrUtil.blankToDefault(app.getAppName(), "应用")).append("\n\n");
        markdown.append("## 对话记录\n\n");

        for (ChatHistory chatHistory : chatHistories) {
            markdown.append("### ")
                    .append(resolveRoleTitle(chatHistory.getMessageType()))
                    .append("\n\n")
                    .append(StrUtil.blankToDefault(chatHistory.getMessage(), ""))
                    .append("\n\n");
        }

        markdown.append("## 源码附录\n\n");
        for (File sourceFile : sourceFiles) {
            String relativePath = normalizePath(FileUtil.subPath(sourceDir.getAbsolutePath(), sourceFile.getAbsolutePath()));
            String language = mapMarkdownLanguage(sourceFile);
            String content = FileUtil.readString(sourceFile, StandardCharsets.UTF_8);

            markdown.append("### ").append(relativePath).append("\n\n");
            markdown.append("```").append(language).append("\n");
            markdown.append(content);
            if (!content.endsWith("\n")) {
                markdown.append("\n");
            }
            markdown.append("```\n\n");
        }

        return markdown.toString();
    }

    private String resolveRoleTitle(String messageType) {
        ChatHistoryMessageTypeEnum typeEnum = ChatHistoryMessageTypeEnum.getEnumByValue(messageType);
        if (typeEnum == ChatHistoryMessageTypeEnum.USER) {
            return "用户";
        }
        return "AI";
    }

    private String mapMarkdownLanguage(File file) {
        String extension = FileUtil.extName(file).toLowerCase(Locale.ROOT);
        return switch (extension) {
            case "html", "htm" -> "html";
            case "css" -> "css";
            case "js" -> "javascript";
            case "jsx" -> "jsx";
            case "ts" -> "typescript";
            case "tsx" -> "tsx";
            case "vue" -> "vue";
            case "json" -> "json";
            case "md", "markdown" -> "markdown";
            case "yaml", "yml" -> "yaml";
            case "xml" -> "xml";
            default -> "";
        };
    }

    private String sanitizeFileName(String fileName) {
        return fileName.replaceAll("[\\\\/:*?\"<>|]", "_");
    }

    private String normalizePath(String path) {
        return path.replace("\\", "/");
    }
}

