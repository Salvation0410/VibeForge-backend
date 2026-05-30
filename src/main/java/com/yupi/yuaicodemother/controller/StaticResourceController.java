package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.constant.AppConstant;
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

@RestController
@RequestMapping("/static")
public class StaticResourceController {

    private static final String PREVIEW_ROOT_DIR = AppConstant.CODE_OUTPUT_ROOT_DIR;
    private static final Logger log = LoggerFactory.getLogger(StaticResourceController.class);

    @Resource
    private VueProjectBuilder vueProjectBuilder;

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

            File projectDir = new File(PREVIEW_ROOT_DIR, deployKey);
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
        } catch (Exception e) {
            log.error("访问静态资源失败", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).build();
        }
    }

    private File resolvePreviewFile(File projectDir, String resourcePath, boolean isVueProject) {
        File file = new File(projectDir, trimLeadingSlash(resourcePath));
        if (file.isFile()) {
            return file;
        }
        if (!isVueProject) {
            return file;
        }

        boolean shouldBuildForPreview = resourcePath.startsWith("/dist/") || "/index.html".equals(resourcePath);
        if (shouldBuildForPreview && vueProjectBuilder.ensureProjectBuilt(projectDir.getAbsolutePath())) {
            File rebuiltFile = new File(projectDir, trimLeadingSlash(resourcePath));
            if (rebuiltFile.isFile()) {
                return rebuiltFile;
            }
            if ("/index.html".equals(resourcePath)) {
                File distIndexFile = new File(vueProjectBuilder.getDistDirectory(projectDir), "index.html");
                if (distIndexFile.isFile()) {
                    return distIndexFile;
                }
            }
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
