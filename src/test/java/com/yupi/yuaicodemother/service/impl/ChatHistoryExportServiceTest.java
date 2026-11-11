package com.yupi.yuaicodemother.service.impl;

import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.ChatHistory;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.AppService;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class ChatHistoryExportServiceTest {
    @TempDir
    Path temporary;

    private ChatHistoryExportServiceImpl service(CodeGenTypeEnum type) {
        App app = new App();
        app.setId(42L);
        app.setUserId(7L);
        app.setCodeGenType(type.getValue());
        app.setAppName("源码交付测试");
        ChatHistory history = new ChatHistory();
        history.setMessageType("user");
        history.setMessage("请保留这条需求");
        AppService apps = mock(AppService.class);
        ChatHistoryService histories = mock(ChatHistoryService.class);
        ArtifactPathResolver paths = mock(ArtifactPathResolver.class);
        when(apps.getById(42L)).thenReturn(app);
        when(histories.list(any(com.mybatisflex.core.query.QueryWrapper.class))).thenReturn(List.of(history));
        when(paths.resolveActiveRoot(type, 42L)).thenReturn(temporary);
        return new ChatHistoryExportServiceImpl(apps, histories, paths);
    }

    private SysUser owner() {
        SysUser user = new SysUser();
        user.setId(7L);
        user.setUserRole("user");
        return user;
    }

    private void write(String name, String content) throws Exception {
        Path file = temporary.resolve(name);
        Files.createDirectories(file.getParent());
        Files.writeString(file, content);
    }

    @ParameterizedTest
    @EnumSource(CodeGenTypeEnum.class)
    void exportsOnlyProjectTextAndPreservesLockFiles(CodeGenTypeEnum type) throws Exception {
        write("index.html", "PROJECT_ENTRY");
        write("src/App.vue", "PROJECT_COMPONENT");
        write("docs/guide.md", "PROJECT_GUIDE");
        write("package-lock.json", "PROJECT_LOCK");
        // 同时覆盖根目录和嵌套依赖，不能先全量扫描再仅按扩展名筛选。
        for (String path : List.of("node_modules/vue/index.js", "packages/widget/node_modules/lib/index.js",
                "dist/assets/index.js", "build/output.json", "target/classes/app.json", ".git/config.json",
                ".idea/workspace.xml", ".vscode/settings.json", ".mvn/settings.xml")) {
            write(path, "EXCLUDED_DEPENDENCY_OR_BUILD");
        }
        write("assets/logo.png", "BINARY_IMAGE");

        String markdown = service(type).exportAppChatHistoryAsMarkdown(42L, owner());
        assertTrue(markdown.contains("请保留这条需求"));
        for (String marker : List.of("PROJECT_ENTRY", "PROJECT_COMPONENT", "PROJECT_GUIDE", "PROJECT_LOCK")) {
            assertTrue(markdown.contains(marker), marker);
        }
        assertFalse(markdown.contains("EXCLUDED_DEPENDENCY_OR_BUILD"));
        assertFalse(markdown.contains("BINARY_IMAGE"));
        assertFalse(markdown.contains("node_modules/"));
        assertTrue(markdown.indexOf("### docs/guide.md") < markdown.indexOf("### index.html"));
    }

    @Test
    void dependenciesAloneDoNotQualifyAsGeneratedSource() throws Exception {
        write("node_modules/vue/index.js", "DEPENDENCY_ONLY");
        write("dist/assets/index.js", "BUILD_ONLY");
        assertThrows(BusinessException.class,
                () -> service(CodeGenTypeEnum.VUE_PROJECT).exportAppChatHistoryAsMarkdown(42L, owner()));
    }
}
