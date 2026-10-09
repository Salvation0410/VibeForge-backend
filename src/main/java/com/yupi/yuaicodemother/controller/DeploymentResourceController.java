package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.constant.AppConstant;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaTypeFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.HandlerMapping;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.net.URI;

/** 提供部署后的静态产物；与可变源码及预览目录完全隔离。 */
@RestController
public class DeploymentResourceController {
    private final Path root;

    public DeploymentResourceController() {
        this(Path.of(AppConstant.CODE_DEPLOY_ROOT_DIR));
    }

    DeploymentResourceController(Path root) {
        this.root = root.toAbsolutePath().normalize();
    }

    @GetMapping("/deployed/{key}/**")
    public ResponseEntity<org.springframework.core.io.Resource> serve(
            @PathVariable String key, HttpServletRequest request) throws IOException {
        String path = (String) request.getAttribute(HandlerMapping.PATH_WITHIN_HANDLER_MAPPING_ATTRIBUTE);
        String relative = path.substring(("/deployed/" + key).length());
        if (!key.matches("[a-zA-Z0-9]{6}")) return missing();
        if (relative.isEmpty()) {
            return ResponseEntity.status(302).location(URI.create(request.getRequestURI() + "/"))
                    .header(HttpHeaders.CACHE_CONTROL, "no-store").build();
        }
        Path app = root.resolve(key);
        Path file = app.resolve(relative.equals("/") ? "index.html" : relative.substring(1)).normalize();
        // 同时检查规范路径与真实路径，禁止穿越目录或借助符号链接读取源码/系统文件。
        if (!file.startsWith(app) || !Files.isRegularFile(file)
                || !app.toRealPath().startsWith(root.toRealPath())
                || !file.toRealPath().startsWith(app.toRealPath())) return missing();
        FileSystemResource resource = new FileSystemResource(file);
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store")
                .contentType(MediaTypeFactory.getMediaType(resource).orElse(org.springframework.http.MediaType.APPLICATION_OCTET_STREAM))
                .body(resource);
    }

    private ResponseEntity<org.springframework.core.io.Resource> missing() {
        // 不缓存部署前或重新部署期间的 404，避免产物到位后仍展示旧错误。
        return ResponseEntity.status(404).header(HttpHeaders.CACHE_CONTROL, "no-store").build();
    }
}
