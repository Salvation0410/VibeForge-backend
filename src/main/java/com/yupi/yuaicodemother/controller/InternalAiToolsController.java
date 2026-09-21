package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.ai.gateway.InternalAiTool;
import com.yupi.yuaicodemother.ai.gateway.ToolInvocationIdempotencyService;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublicationService;
import com.yupi.yuaicodemother.core.artifact.ArtifactValidationException;
import com.yupi.yuaicodemother.core.artifact.HtmlArtifactParser;
import com.yupi.yuaicodemother.core.artifact.HtmlArtifactValidator;
import com.yupi.yuaicodemother.core.artifact.MultiFileArtifactValidator;
import com.yupi.yuaicodemother.core.paser.MultiFileCodeParser;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
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

/**
 * 独立 Python AI 服务调用的 Spring 文件与构建工具边界。
 * <p>
 * 控制器统一负责内部鉴权、工具调用幂等、应用目录沙箱和危险文件保护，
 * 避免 Python 服务直接访问项目文件系统。
 */
@RestController
@RequestMapping("/internal/ai-tools")
@RequiredArgsConstructor
public class InternalAiToolsController {
    private static final String MULTI_FILE_RELEASE_IMMUTABLE = "MULTI_FILE_RELEASE_IMMUTABLE";
    private static final String[] IMPORTANT_FILES = {"package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
            "vite.config.js", "vite.config.ts", "vue.config.js", "tsconfig.json", "index.html", "main.js", "main.ts", "App.vue"};

    private final AiEngineProperties properties;
    private final VueProjectBuilder projectBuilder;
    private final ArtifactPathResolver artifactPathResolver;
    private final ArtifactPublicationService artifactPublicationService;
    private final MultiFileArtifactValidator artifactValidator;
    private final HtmlArtifactValidator htmlArtifactValidator;
    private final ToolInvocationIdempotencyService idempotencyService;

    /**
     * 校验内部调用身份，并通过共享幂等边界执行指定工具。
     *
     * @param authorization Python 服务携带的 Bearer 认证头
     * @param request 工具调用 ID、工具名称和参数
     * @return Spring 统一响应包装的工具执行结果
     * @throws BusinessException 认证失败、请求参数无效或工具执行失败时抛出
     */
    @PostMapping("/invoke")
    public BaseResponse<Map<String, Object>> invoke(@RequestHeader(value = "Authorization", required = false) String authorization,
                                                    @RequestBody ToolRequest request) {
        authenticate(authorization);
        if (request == null || request.requestId() == null || request.requestId().isBlank()
                || request.toolCallId() == null || request.toolCallId().isBlank()
                || request.toolName() == null || request.toolName().isBlank()) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "requestId, toolCallId and toolName are required");
        }
        InternalAiTool tool = parseTool(request.toolName());
        Map<String, Object> arguments = request.arguments() == null ? Map.of() : request.arguments();
        Map<String, Object> result = idempotencyService.execute(
                request.appId(),
                request.requestId(),
                request.toolCallId(),
                tool,
                arguments,
                () -> execute(tool, request.appId(), request.requestId(), arguments));
        return ResultUtils.success(result);
    }

    /**
     * 根据工具名称分发到具体的文件、产物校验或项目构建方法。
     *
     * @param tool 已规范化的内部工具
     * @param appId 顶层请求中的应用 ID
     * @param requestId 顶层请求中的生成请求 ID
     * @param args 工具参数
     * @return 具体工具产生的结构化结果
     * @throws BusinessException 工具执行参数无效时抛出
     */
    private Map<String, Object> execute(
            InternalAiTool tool,
            long appId,
            String requestId,
            Map<String, Object> args) {
        return switch (tool) {
            case FILE_READ -> Map.of("content", readFile(sandboxPath(appId, args, "relativeFilePath", false)));
            case DIR_READ -> Map.of("entries", readDir(sandboxPath(appId, args, "relativeDirPath", true)));
            case FILE_WRITE -> writeFile(appId, args);
            case FILE_MODIFY -> modifyFile(appId, args);
            case FILE_DELETE -> deleteFile(appId, args);
            case ARTIFACT_VALIDATE -> validateArtifact(args);
            case ARTIFACT_PUBLISH -> publishArtifact(appId, requestId, args);
            case PROJECT_BUILD -> buildProject(appId, args);
        };
    }

    /**
     * 在应用沙箱内创建或覆盖 UTF-8 文件，并按需创建父目录。
     *
     * @param appId 应用 ID，用于确定独立项目目录
     * @param args 包含相对文件路径、文件内容和代码生成类型的参数
     * @return 写入状态和目标文件名
     * @throws BusinessException 路径非法或文件写入失败时抛出
     */
    private Map<String, Object> writeFile(long appId, Map<String, Object> args) {
        rejectMultiFileMutation(args);
        Path path = sandboxPath(appId, args, "relativeFilePath", false);
        String content = text(args.get("content"));
        try {
            Files.createDirectories(path.getParent());
            Files.writeString(path, content, StandardCharsets.UTF_8, StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING);
            return Map.of("ok", true, "path", path.getFileName().toString());
        } catch (Exception e) { throw failure("file write failed", e); }
    }

    /**
     * 在应用沙箱内用新内容替换文件中的指定旧内容。
     *
     * @param appId 应用 ID，用于确定独立项目目录
     * @param args 包含相对文件路径、旧内容、新内容和代码生成类型的参数
     * @return 是否修改成功；找不到旧内容时返回失败说明
     * @throws BusinessException 路径非法、文件不存在或读写失败时抛出
     */
    private Map<String, Object> modifyFile(long appId, Map<String, Object> args) {
        rejectMultiFileMutation(args);
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

    /**
     * 删除应用沙箱内的普通文件，并阻止删除项目关键入口和构建配置文件。
     *
     * @param appId 应用 ID，用于确定独立项目目录
     * @param args 包含相对文件路径和代码生成类型的参数
     * @return 文件是否实际被删除
     * @throws BusinessException 路径非法、文件受保护或删除失败时抛出
     */
    private Map<String, Object> deleteFile(long appId, Map<String, Object> args) {
        rejectMultiFileMutation(args);
        Path path = sandboxPath(appId, args, "relativeFilePath", false);
        String name = path.getFileName().toString();
        for (String important : IMPORTANT_FILES) if (important.equalsIgnoreCase(name))
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "Protected project file cannot be deleted");
        try { return Map.of("ok", Files.deleteIfExists(path)); }
        catch (Exception e) { throw failure("file delete failed", e); }
    }

    /**
     * 按生成类型对候选执行确定性校验；HTML 和多文件解析失败均返回结构化错误供修复。
     *
     * @param args 包含生成类型和待校验 artifact 的工具参数
     * @return 校验状态和稳定错误列表
     */
    private Map<String, Object> validateArtifact(Map<String, Object> args) {
        String artifact = text(args.get("artifact"));
        CodeGenTypeEnum type = artifactType(args);
        if (type != CodeGenTypeEnum.HTML && type != CodeGenTypeEnum.MULTI_FILE) {
            return Map.of("valid", !artifact.isBlank(), "errors", artifact.isBlank()
                    ? java.util.List.of(Map.of("code", "ARTIFACT_MISSING", "message", "artifact is blank")) : java.util.List.of());
        }
        try {
            var result = type == CodeGenTypeEnum.HTML
                    ? htmlArtifactValidator.validate(new HtmlArtifactParser().parse(artifact))
                    : artifactValidator.validate(new MultiFileCodeParser().parseCode(artifact));
            return Map.of("valid", result.valid(), "errors", result.errors());
        } catch (ArtifactValidationException e) {
            Map<String, Object> error = new HashMap<>();
            error.put("code", e.getErrorCode()); error.put("message", e.getMessage());
            if (e.getFile() != null) error.put("file", e.getFile());
            return Map.of("valid", false, "errors", java.util.List.of(error));
        }
    }

    /**
     * 将 Python 提交的最终候选交给 Spring 重新校验并版本化发布。
     *
     * @param appId 应用 ID
     * @param requestId 顶层请求中的生成请求 ID
     * @param args 包含 artifact、engine 和 finishReason 的工具参数
     * @return 发布状态、版本 ID 和文件摘要
     */
    private Map<String, Object> publishArtifact(long appId, String requestId, Map<String, Object> args) {
        CodeGenTypeEnum type = artifactType(args);
        var result = switch (type) {
            case HTML -> artifactPublicationService.publishHtml(appId, requestId,
                    text(args.get("artifact")), text(args.get("engine")), text(args.get("finishReason")));
            case MULTI_FILE -> artifactPublicationService.publishMultiFile(appId, requestId,
                    text(args.get("artifact")), text(args.get("engine")), text(args.get("finishReason")));
            default -> throw new BusinessException(ErrorCode.PARAMS_ERROR, "artifact_publish does not support " + type);
        };
        return Map.of("published", result.published(), "versionId", result.versionId(), "hashes", result.hashes());
    }

    /** 将内部工具的生成类型规范化；未知类型只沿用 Vue 的通用校验，不允许错误发布。 */
    private CodeGenTypeEnum artifactType(Map<String, Object> args) {
        String type = text(args.get("codeGenType")).toLowerCase().replace('-', '_');
        return switch (type) {
            case "html" -> CodeGenTypeEnum.HTML;
            case "multi_file", "multifile" -> CodeGenTypeEnum.MULTI_FILE;
            default -> CodeGenTypeEnum.VUE_PROJECT;
        };
    }

    /**
     * 定位应用项目目录并确保项目完成构建。
     *
     * @param appId 应用 ID，用于确定独立项目目录
     * @param args 包含代码生成类型的工具参数
     * @return 构建状态和项目绝对路径
     */
    private Map<String, Object> buildProject(long appId, Map<String, Object> args) {
        Path root = projectRoot(appId, text(args.get("codeGenType")));
        return Map.of("built", projectBuilder.ensureProjectBuilt(root.toString()), "path", root.toString());
    }

    /**
     * 以 UTF-8 读取已通过沙箱校验的文件。
     *
     * @param path 应用沙箱内的规范化文件路径
     * @return 文件完整文本内容
     * @throws BusinessException 文件读取失败时抛出
     */
    private String readFile(Path path) {
        try { return Files.readString(path, StandardCharsets.UTF_8); }
        catch (Exception e) { throw failure("file read failed", e); }
    }

    /**
     * 递归读取应用沙箱目录，并返回按路径排序的普通文件相对路径。
     *
     * @param path 已通过沙箱校验的目录路径
     * @return 目录下所有普通文件的相对路径列表
     * @throws BusinessException 目录遍历失败时抛出
     */
    private java.util.List<String> readDir(Path path) {
        try (var stream = Files.walk(path)) {
            return stream.filter(Files::isRegularFile).sorted(Comparator.comparing(Path::toString))
                    .map(p -> path.relativize(p).toString()).toList();
        } catch (Exception e) { throw failure("directory read failed", e); }
    }

    /**
     * 将工具传入的相对路径解析到对应应用根目录，并阻止绝对路径和目录穿越。
     *
     * @param appId 应用 ID，用于确定独立项目目录
     * @param args 包含相对路径和代码生成类型的工具参数
     * @param key 相对路径在参数 Map 中的字段名
     * @param directory 是否允许空路径表示项目根目录
     * @return 位于应用沙箱内的规范化绝对路径
     * @throws BusinessException 路径缺失、使用绝对路径或越过应用根目录时抛出
     */
    private Path sandboxPath(long appId, Map<String, Object> args, String key, boolean directory) {
        String relative = text(args.get(key));
        if (relative.isBlank() && !directory) throw new BusinessException(ErrorCode.PARAMS_ERROR, key + " is required");
        if (Path.of(relative).isAbsolute()) throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "Absolute paths are forbidden");
        Path root = projectRoot(appId, text(args.get("codeGenType")));
        Path path = root.resolve(relative).normalize();
        if (!path.startsWith(root)) throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "Path escapes app sandbox");
        return path;
    }

    /**
     * 阻止通用文件工具修改多文件活动版本，确保发布目录只能由原子发布流程创建和切换。
     *
     * @param args 包含代码生成类型的工具参数
     * @throws BusinessException 请求试图写入、修改或删除 MULTI_FILE 产物时，以稳定错误消息拒绝
     */
    private void rejectMultiFileMutation(Map<String, Object> args) {
        String type = text(args.get("codeGenType")).toLowerCase().replace('-', '_');
        if ("multi_file".equals(type) || "multifile".equals(type)) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, MULTI_FILE_RELEASE_IMMUTABLE);
        }
    }

    /**
     * 根据应用 ID 和代码生成类型计算该应用唯一的代码输出根目录。
     *
     * @param appId 应用 ID
     * @param codeGenType HTML、多文件或 Vue 项目类型；未知类型按 Vue 项目处理
     * @return 规范化后的项目绝对路径
     */
    private Path projectRoot(long appId, String codeGenType) {
        String type = codeGenType == null ? "" : codeGenType.toLowerCase().replace('-', '_');
        CodeGenTypeEnum generationType = switch (type) {
            case "html" -> CodeGenTypeEnum.HTML;
            case "multi_file", "multifile" -> CodeGenTypeEnum.MULTI_FILE;
            default -> CodeGenTypeEnum.VUE_PROJECT;
        };
        // 内部读取工具与预览共享同一个已提交版本解析规则。
        return artifactPathResolver.resolveActiveRoot(generationType, appId);
    }

    /**
     * 使用常量时间字节比较校验内部 Bearer 令牌。
     *
     * @param authorization HTTP Authorization 请求头
     * @throws BusinessException 服务令牌未配置、请求未携带令牌或令牌不匹配时抛出
     */
    private void authenticate(String authorization) {
        String expected = "Bearer " + properties.getToken();
        if (properties.getToken() == null || properties.getToken().isBlank()
                || authorization == null || !MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8), authorization.getBytes(StandardCharsets.UTF_8))) {
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "Invalid internal bearer token");
        }
    }

    /** 将外部工具名（含历史别名）规范化为内部枚举。 */
    private InternalAiTool parseTool(String toolName) {
        try {
            return InternalAiTool.fromExternalName(toolName);
        } catch (IllegalArgumentException exception) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, exception.getMessage());
        }
    }

    /**
     * 将可空参数转换为字符串，空值统一转换为空字符串。
     *
     * @param value 待转换的参数值
     * @return 非空字符串表示
     */
    private String text(Object value) { return value == null ? "" : String.valueOf(value); }

    /**
     * 将底层文件或构建异常转换为统一业务异常，并保留简要原因。
     *
     * @param message 工具操作失败的业务描述
     * @param cause 底层异常
     * @return 可由全局异常处理器转换为统一响应的业务异常
     */
    private BusinessException failure(String message, Exception cause) {
        return new BusinessException(ErrorCode.OPERATION_ERROR, message + ": " + cause.getMessage());
    }

    /**
     * Python AI 服务提交的内部工具调用请求。
     *
     * @param appId 应用 ID，用于限定沙箱和幂等作用域
     * @param requestId 生成请求 ID，用于限定幂等作用域
     * @param toolCallId 工具调用唯一标识
     * @param toolName 要执行的工具名称
     * @param arguments 工具参数
     */
    public record ToolRequest(
            long appId,
            String requestId,
            String toolCallId,
            String toolName,
            Map<String, Object> arguments) { }
}
