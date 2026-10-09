package com.yupi.yuaicodemother.controller;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.servlet.HandlerMapping;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;

class DeploymentResourceControllerTest {
    @TempDir Path root;

    private MockHttpServletRequest request(String suffix) {
        MockHttpServletRequest request = new MockHttpServletRequest("GET", "/api/deployed/abc123" + suffix);
        request.setAttribute(HandlerMapping.PATH_WITHIN_HANDLER_MAPPING_ATTRIBUTE, "/deployed/abc123" + suffix);
        return request;
    }

    @Test
    void servesIndexAndAssetsWithoutNginx() throws Exception {
        Path app = Files.createDirectories(root.resolve("abc123/assets"));
        Files.writeString(app.getParent().resolve("index.html"), "<html>hello</html>");
        Files.writeString(app.resolve("app.js"), "console.log('hello')");
        var controller = new DeploymentResourceController(root);
        assertEquals(200, controller.serve("abc123", request("/")).getStatusCode().value());
        assertEquals(200, controller.serve("abc123", request("/assets/app.js")).getStatusCode().value());
        assertEquals("/api/deployed/abc123/", controller.serve("abc123", request("")).getHeaders().getLocation().toString());
    }

    @Test
    void blocksTraversalAndDoesNotCacheMissingFiles() throws Exception {
        Files.createDirectories(root.resolve("abc123"));
        Files.writeString(root.resolve("secret.txt"), "secret");
        var controller = new DeploymentResourceController(root);
        assertEquals(404, controller.serve("abc123", request("/../secret.txt")).getStatusCode().value());
        var missing = controller.serve("abc123", request("/assets/missing.js"));
        assertEquals(404, missing.getStatusCode().value());
        assertEquals("no-store", missing.getHeaders().getCacheControl());
    }
}
