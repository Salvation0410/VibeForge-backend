package com.yupi.yuaicodemother.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.constant.AppConstant;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** 独立 AI 服务调用的 Spring 文件/构建工具边界。 */
@RestController
@RequestMapping("/internal/ai-tools")
@RequiredArgsConstructor
public class InternalAiToolsController {
    private static final Map<String, String> IDEMPOTENT_RESULTS = new ConcurrentHashMap<>();
    private static final String[] IMPORTANT_FILES = {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
            "vite.config.js", "vite.config.ts", "vue.config.js", "tsconfig.json", "index.html", "main.js", "main.ts", "App.vue"};

    private final AiEngineProperties properties;
    private final VueProjectBuilder projectBuilder;
    private final ObjectMapper objectMapper;

    @PostMapping("/invoke")
    public BaseResponse<Map<String, Object>> invoke(@RequestHeader(value = "Authorization", required = false) String authorization,
                                                    @RequestBody ToolRequest request) {
        authenticate(authorization);
        if (request == null || request.toolCallId() == null || request.toolCallId().isBlank()
                || request.toolName() == null || request.toolName().isBlank()) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "toolCallId and toolName are required");
        }
        String key = request.toolCallId();
        String cached = IDEMPOTENT_RESULTS.get(key);
        if (cached != null) return ResultUtils.success(readResult(cached));
        synchronized (IDEMPOTENT_RESULTS) {
            cached = IDEMPOTENT_RESULTS.get(key);
            if (cached != null) return ResultUtils.success(readResult(cached));
            Map<String, Object> result = execute(request.toolName(), request.arguments() == null ? Map.of() : request.arguments());
            try {
                String encoded = objectMapper.writeValueAsString(result);
                IDEMPOTENT_RESULTS.put(key, encoded);
                return ResultUtils.success(result);
            } catch (Exception e) {
                throw new BusinessException(ErrorCode.SYSTEM_ERROR, "Failed to cache tool result");
            }
        }
    }

    private Map<String, Object> execute(String toolName, Map<String, Object> args) {
        long appId = number(args.get("appId"), "appId");
        return switch (toolName) {
            case "file_read", "readFile", "read_file" -> Map.of("content", readFile(sandboxPath(appId, args, "relativeFilePath", false)));
            case "dir_read", "readDir", "read_dir" -> Map.of("entries", readDir(sandboxPath(appId, args, "relativeDirPath", true)));
            case "file_write", "writeFile", "write_file" -> writeFile(appId, args);
            case "file_modify", "modifyFile", "modify_file" -> modifyFile(appId, args);
            case "file_delete", "deleteFile", "delete_file" -> deleteFile(appId, args);
            case "artifact_validate", "artifact_validation" -> validateArtifact(args);
            case "project_build" -> buildProject(appId, args);
            default -> throw new BusinessException(ErrorCode.PARAMS_ERROR, "Unsupported tool: " + toolName);
        };
    }

    private Map<String, Object> writeFile(long appId, Map<String, Object> args) {
        Path path = sandboxPath(appId, args, "relativeFilePath", false);
        String content = text(args.get("content"));
        try {
            Files.createDirectories(path.getParent());
            Files.writeString(path, content, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
            return Map.of("ok", true, "path", path.getFileName().toString());
        } catch (Exception e) { throw failure("file write failed", e); }
    }

    private Map<String, Object> modifyFile(long appId, Map<String, Object> args) {
        Path path = sandboxPath(appId, args, "relativeFilePath", false);
        try {
            String original = Files.readString(path, StandardCharsets.UTF_8);
            String oldContent = text(args.get("oldContent"));
            if (!original.contains(oldContent)) return Map.of("ok", false, "message", "oldContent not found");
            Files.writeString(path, original.replace(oldContent, text(args.get("newContent"))), StandardCharsets.UTF_8,
                    StandardOpenOption.TRUNCATE_EXISTING);
            return Map.of("ok", true);
        } catch (Exception e) { throw failure("file modify failed", e); }
    }

    private Map<String, Object> deleteFile(long appId, Map<String, Object> args) {
        Path path = sandboxPath(appId, args, "relativeFilePath", false);
        String name = path.getFileName().toString();
        for (String important : IMPORTANT_FILES) if (important.equalsIgnoreCase(name))
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "Protected project file cannot be deleted");
        try { return Map.of("ok", Files.deleteIfExists(path)); }
        catch (Exception e) { throw failure("file delete failed", e); }
    }

    private Map<String, Object> validateArtifact(Map<String, Object> args) {
        String artifact = text(args.get("artifact"));
        return Map.of("valid", !artifact.isBlank(), "errors", artifact.isBlank() ? java.util.List.of("artifact is blank") : java.util.List.of());
    }

    private Map<String, Object> buildProject(long appId, Map<String, Object> args) {
        Path root = projectRoot(appId, text(args.get("codeGenType")));
        return Map.of("built", projectBuilder.ensureProjectBuilt(root.toString()), "path", root.toString());
    }

    private String readFile(Path path) {
        try { return Files.readString(path, StandardCharsets.UTF_8); }
        catch (Exception e) { throw failure("file read failed", e); }
    }

    private java.util.List<String> readDir(Path path) {
        try (var stream = Files.walk(path)) {
            return stream.filter(Files::isRegularFile).sorted(Comparator.comparing(Path::toString))
                    .map(p -> path.relativize(p).toString()).toList();
        } catch (Exception e) { throw failure("directory read failed", e); }
    }

    private Path sandboxPath(long appId, Map<String, Object> args, String key, boolean directory) {
        String relative = text(args.get(key));
        if (relative.isBlank() && !directory) throw new BusinessException(ErrorCode.PARAMS_ERROR, key + " is required");
        if (Path.of(relative).isAbsolute()) throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "Absolute paths are forbidden");
        Path root = projectRoot(appId, text(args.get("codeGenType")));
        Path path = root.resolve(relative).normalize();
        if (!path.startsWith(root)) throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "Path escapes app sandbox");
        return path;
    }

    private Path projectRoot(long appId, String codeGenType) {
        String type = codeGenType == null ? "" : codeGenType.toLowerCase().replace('-', '_');
        String name = switch (type) {
            case "html" -> "html_" + appId;
            case "multi_file", "multifile" -> "multi_file_" + appId;
            default -> "vue_project_" + appId;
        };
        return Path.of(AppConstant.CODE_OUTPUT_ROOT_DIR, name).toAbsolutePath().normalize();
    }

    private void authenticate(String authorization) {
        String expected = "Bearer " + properties.getToken();
        if (properties.getToken() == null || properties.getToken().isBlank()
                || authorization == null || !MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8), authorization.getBytes(StandardCharsets.UTF_8))) {
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "Invalid internal bearer token");
        }
    }

    private long number(Object value, String key) {
        try { return Long.parseLong(text(value)); }
        catch (Exception e) { throw new BusinessException(ErrorCode.PARAMS_ERROR, key + " is required"); }
    }

    private String text(Object value) { return value == null ? "" : String.valueOf(value); }

    private Map<String, Object> readResult(String value) {
        try { return objectMapper.readValue(value, Map.class); }
        catch (Exception e) { throw new BusinessException(ErrorCode.SYSTEM_ERROR, "Invalid cached tool result"); }
    }

    private BusinessException failure(String message, Exception cause) {
        return new BusinessException(ErrorCode.OPERATION_ERROR, message + ": " + cause.getMessage());
    }

    public record ToolRequest(String toolCallId, String toolName, Map<String, Object> arguments) { }
}
