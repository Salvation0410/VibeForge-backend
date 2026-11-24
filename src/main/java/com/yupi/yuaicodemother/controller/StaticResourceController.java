package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import jakarta.annotation.Resource;
import jakarta.servlet.http.HttpServletRequest;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.HandlerMapping;

import java.io.File;
import java.io.IOException;
import java.nio.file.Path;

@RestController
@RequestMapping("/static")
public class StaticResourceController {

    private static final Logger log = LoggerFactory.getLogger(StaticResourceController.class);

    @Resource
    private VueProjectBuilder vueProjectBuilder;
    @Resource
    private ArtifactPathResolver artifactPathResolver;

    /**
     * 从应用当前已提交版本提供预览资源，避免生成中的候选文件被外部读取。
     *
     * @param deployKey 应用产物目录标识
     * @param request 当前 HTTP 请求
     * @return 资源响应；路径无效或读取失败时返回对应错误状态
     */
    @GetMapping("/{deployKey}/**")
    public ResponseEntity<org.springframework.core.io.Resource> serveStaticResource(
            @PathVariable String deployKey,
            HttpServletRequest request) {
        try {
            String resourcePath = (String) request.getAttribute(HandlerMapping.PATH_WITHIN_HANDLER_MAPPING_ATTRIBUTE);
            resourcePath = resourcePath.substring(("/static/" + deployKey).length());

            if (resourcePath.isEmpty()) {
                HttpHeaders headers = new HttpHeaders();
                headers.add("Location", request.getRequestURI() + "/");
                return new ResponseEntity<>(headers, HttpStatus.MOVED_PERMANENTLY);
            }

            // 多文件预览必须读取已提交版本，不能直接读取可变应用根目录。
            File projectDir = artifactPathResolver.resolveDirectoryName(deployKey).toFile();
            boolean isVueProject = vueProjectBuilder.isVueProject(projectDir);

            if ("/".equals(resourcePath)) {
                resourcePath = isVueProject ? "/dist/index.html" : "/index.html";
            }

            File file = resolvePreviewFile(projectDir, resourcePath, isVueProject);
            if (!file.isFile()) {
                return ResponseEntity.notFound().build();
            }

            org.springframework.core.io.Resource resource = new FileSystemResource(file);
            return ResponseEntity.ok()
                    .header(HttpHeaders.CONTENT_TYPE, getContentTypeWithCharset(file.getName()))
                    .body(resource);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.notFound().build();
        } catch (Exception e) {
            log.error("访问静态资源失败", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        }
    }

    private File resolvePreviewFile(File projectDir, String resourcePath, boolean isVueProject) throws IOException {
        File file = resolveSandboxFile(projectDir, resourcePath);
        if (file.isFile()) {
            return file;
        }
        if (!isVueProject) {
            return file;
        }

        // 根入口 HTML 使用 dist 时，其相对资源 URL 仍从工程根发起；仅在原目标不存在时映射。
        boolean explicitDistPath = resourcePath.startsWith("/dist/");
        if (!explicitDistPath) {
            File distFile = resolveSandboxFile(projectDir, "/dist/" + trimLeadingSlash(resourcePath));
            if (distFile.isFile()) {
                return distFile;
            }
        }

        // 只有入口缺失可以触发构建，未知资源不能启动 npm 或被替换为 HTML。
        boolean shouldBuildForPreview = "/dist/index.html".equals(resourcePath) || "/index.html".equals(resourcePath);
        if (shouldBuildForPreview && vueProjectBuilder.ensureProjectBuilt(projectDir.getAbsolutePath())) {
            File rebuiltFile = resolveSandboxFile(projectDir, resourcePath);
            if (rebuiltFile.isFile()) {
                return rebuiltFile;
            }
            if ("/index.html".equals(resourcePath)) {
                File distIndexFile = resolveSandboxFile(projectDir, "/dist/index.html");
                if (distIndexFile.isFile()) {
                    return distIndexFile;
                }
            }
        }

        return file;
    }

    /** 直读与 dist 映射共用同一沙箱，拒绝穿越、Windows 分隔符与逃逸符号链接。 */
    private File resolveSandboxFile(File projectDir, String resourcePath) throws IOException {
        String relativePath = trimLeadingSlash(resourcePath);
        if (relativePath.contains("\\") || relativePath.contains(":")) {
            throw new IllegalArgumentException("非法的预览资源路径");
        }
        Path relative = Path.of(relativePath);
        if (relative.isAbsolute()) {
            throw new IllegalArgumentException("预览资源必须使用相对路径");
        }
        for (Path segment : relative) {
            if ("..".equals(segment.toString())) {
                throw new IllegalArgumentException("预览资源不能跨越目录");
            }
        }
        Path sandbox = projectDir.getCanonicalFile().toPath();
        File file = sandbox.resolve(relative).toFile().getCanonicalFile();
        if (!file.toPath().startsWith(sandbox)) {
            throw new IllegalArgumentException("预览资源超出应用沙箱");
        }
        return file;
    }

    private String trimLeadingSlash(String path) {
        if (path == null || path.isEmpty()) {
            return path;
        }
        return path.startsWith("/") ? path.substring(1) : path;
    }

    private String getContentTypeWithCharset(String fileName) {
        if (fileName.endsWith(".html")) return "text/html; charset=UTF-8";
        if (fileName.endsWith(".css")) return "text/css; charset=UTF-8";
        if (fileName.endsWith(".js")) return "application/javascript; charset=UTF-8";
        if (fileName.endsWith(".png")) return "image/png";
        if (fileName.endsWith(".jpg") || fileName.endsWith(".jpeg")) return "image/jpeg";
        if (fileName.endsWith(".svg")) return "image/svg+xml";
        return "application/octet-stream";
    }
}
