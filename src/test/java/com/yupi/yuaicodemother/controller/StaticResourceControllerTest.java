package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.nio.file.Files;
import java.nio.file.Path;
import java.net.URI;

import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

class StaticResourceControllerTest {
    private static final String PREVIEW = "/static/vue_project_42";
    @TempDir Path root;
    private Path project;
    private VueProjectBuilder builder;
    private MockMvc mvc;

    @BeforeEach
    void setUp() throws Exception {
        project = Files.createDirectories(root.resolve("vue_project_42"));
        Files.writeString(project.resolve("package.json"), "{}");
        builder = spy(new VueProjectBuilder());
        // HTTP 测试只允许显式的入口构建夹具，不执行 npm 或读取真实生成工程。
        doReturn(false).when(builder).ensureProjectBuilt(anyString());
        ArtifactPathResolver resolver = mock(ArtifactPathResolver.class);
        when(resolver.resolveDirectoryName("vue_project_42")).thenReturn(project);
        StaticResourceController controller = new StaticResourceController();
        ReflectionTestUtils.setField(controller, "vueProjectBuilder", builder);
        ReflectionTestUtils.setField(controller, "artifactPathResolver", resolver);
        mvc = MockMvcBuilders.standaloneSetup(controller).build();
    }

    @Test
    void rootHtmlAndItsRelativeAssetsUseExistingDist() throws Exception {
        String html = "<html><link rel=\"stylesheet\" href=\"./assets/app.css\">"
                + "<script src=\"./assets/app.js\"></script></html>";
        write("dist/index.html", html);
        write("dist/assets/app.js", "console.log('preview')");
        write("dist/assets/app.css", "body { color: blue; }");

        mvc.perform(get(PREVIEW + "/")).andExpect(status().isOk()).andExpect(content().string(html));
        mvc.perform(get(PREVIEW + "/assets/app.js")).andExpect(status().isOk())
                .andExpect(content().contentType("application/javascript;charset=UTF-8"))
                .andExpect(content().string("console.log('preview')"));
        mvc.perform(get(PREVIEW + "/assets/app.css")).andExpect(status().isOk())
                .andExpect(content().contentType("text/css;charset=UTF-8"))
                .andExpect(content().string("body { color: blue; }"));
        verify(builder, never()).ensureProjectBuilt(anyString());
    }

    @Test
    void explicitDistUrlsAndExistingRootResourcesKeepWorking() throws Exception {
        write("dist/index.html", "built page");
        write("dist/assets/app.js", "built asset");
        write("assets/app.js", "root asset");

        mvc.perform(get(PREVIEW + "/dist/index.html")).andExpect(status().isOk())
                .andExpect(content().string("built page"));
        mvc.perform(get(PREVIEW + "/dist/assets/app.js")).andExpect(status().isOk())
                .andExpect(content().string("built asset"));
        mvc.perform(get(PREVIEW + "/assets/app.js")).andExpect(status().isOk())
                .andExpect(content().string("root asset"));
        verify(builder, never()).ensureProjectBuilt(anyString());
    }

    @Test
    void unknownResourcesReturn404WithoutBuildingOrIndexFallback() throws Exception {
        write("dist/index.html", "built page");
        mvc.perform(get(PREVIEW + "/assets/missing.js")).andExpect(status().isNotFound());
        mvc.perform(get(PREVIEW + "/dist/assets/missing.css")).andExpect(status().isNotFound());
        mvc.perform(get(PREVIEW + "/missing-route")).andExpect(status().isNotFound());
        verify(builder, never()).ensureProjectBuilt(anyString());
    }

    @Test
    void missingAssetsDoNotBuildEvenWithoutDist() throws Exception {
        mvc.perform(get(PREVIEW + "/assets/missing.js")).andExpect(status().isNotFound());
        mvc.perform(get(PREVIEW + "/dist/assets/missing.js")).andExpect(status().isNotFound());
        verify(builder, never()).ensureProjectBuilt(anyString());
    }

    @Test
    void missingRootEntryCanBuildOnceAndServeDist() throws Exception {
        doAnswer(invocation -> {
            write("dist/index.html", "new build");
            return true;
        }).when(builder).ensureProjectBuilt(project.toString());
        mvc.perform(get(PREVIEW + "/")).andExpect(status().isOk())
                .andExpect(content().string("new build"));
        verify(builder).ensureProjectBuilt(project.toString());
    }

    @Test
    void missingEntryRemains404WhenBuildFails() throws Exception {
        mvc.perform(get(PREVIEW + "/")).andExpect(status().isNotFound());
        verify(builder).ensureProjectBuilt(project.toString());
    }

    @Test
    void rootWithoutTrailingSlashRedirectsBeforeResolvingAssets() throws Exception {
        mvc.perform(get(PREVIEW)).andExpect(status().isMovedPermanently())
                .andExpect(header().string("Location", PREVIEW + "/"));
        verify(builder, never()).ensureProjectBuilt(anyString());
    }

    @Test
    void explicitIndexUsesExistingDistWithoutBuilding() throws Exception {
        write("dist/index.html", "built page");
        mvc.perform(get(PREVIEW + "/index.html")).andExpect(status().isOk())
                .andExpect(content().string("built page"));
        verify(builder, never()).ensureProjectBuilt(anyString());
    }

    @Test
    void nonVueProjectsDoNotUseDistFallback() throws Exception {
        Files.delete(project.resolve("package.json"));
        write("index.html", "plain page");
        write("dist/assets/app.js", "not public at root");
        mvc.perform(get(PREVIEW + "/")).andExpect(status().isOk())
                .andExpect(content().string("plain page"));
        mvc.perform(get(PREVIEW + "/assets/app.js")).andExpect(status().isNotFound());
        verify(builder, never()).ensureProjectBuilt(anyString());
    }

    @Test
    void traversalCannotEscapeProjectOrDistMapping() throws Exception {
        Files.writeString(root.resolve("secret.txt"), "outside project");
        write("secret.txt", "outside dist");
        write("dist/index.html", "built page");
        mvc.perform(get(PREVIEW + "/../secret.txt")).andExpect(status().isNotFound());
        mvc.perform(get(PREVIEW + "/dist/../../secret.txt")).andExpect(status().isNotFound());
        mvc.perform(get(PREVIEW + "/assets/../../secret.txt")).andExpect(status().isNotFound());
        // 使用原始 URI，确保百分号编码不会被请求构造器再次转义。
        mvc.perform(get(URI.create(PREVIEW + "/assets/%2e%2e/%2e%2e/secret.txt")))
                .andExpect(status().isNotFound());
        mvc.perform(get(URI.create(PREVIEW + "/..%5csecret.txt"))).andExpect(status().isNotFound());
        verify(builder, never()).ensureProjectBuilt(anyString());
    }

    private void write(String relativePath, String content) throws Exception {
        Path target = project.resolve(relativePath);
        Files.createDirectories(target.getParent());
        Files.writeString(target, content);
    }
}
