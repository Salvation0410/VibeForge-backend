package com.yupi.yuaicodemother.utils;

import cn.hutool.core.io.FileUtil;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import javax.imageio.ImageIO;
import java.io.File;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.*;

/** 显式启用的本机浏览器烟测；仅访问回环静态页面，不包含 OSS 上传或封面回写。 */
@EnabledIfEnvironmentVariable(named = "AI_SCREENSHOT_BROWSER_INTEGRATION", matches = "true")
class WebScreenshotBrowserIT {
    @Test
    void capturesLoopbackPageWithExplicitLocalDriver() throws Exception {
        String driverPath = System.getenv("APP_SCREENSHOT_DRIVER_PATH");
        assertNotNull(driverPath, "真实浏览器测试必须设置 APP_SCREENSHOT_DRIVER_PATH");
        assertTrue(new File(driverPath).isFile(), "真实浏览器测试只允许现有本机驱动");
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        byte[] page = "<!doctype html><meta charset='utf-8'><title>Local cover</title><body style='background:#eef2ff'><h1>Local screenshot</h1><button>Local button</button></body>".getBytes(StandardCharsets.UTF_8);
        server.createContext("/", exchange -> {
            exchange.getResponseHeaders().set("Content-Type", "text/html; charset=utf-8");
            exchange.sendResponseHeaders(200, page.length);
            try (var output = exchange.getResponseBody()) { output.write(page); }
        });
        server.start();
        String output = null;
        try {
            output = WebScreenshotUtils.saveWebPageScreenshot("http://127.0.0.1:" + server.getAddress().getPort() + "/",
                    driverPath, ScreenshotTimings.newId());
            assertNotNull(output);
            var image = ImageIO.read(new File(output));
            assertNotNull(image);
            assertTrue(image.getWidth() > 1000);
            assertTrue(image.getHeight() > 500);
        } finally {
            server.stop(0);
            if (output != null) FileUtil.del(new File(output).getParentFile());
        }
    }
}
