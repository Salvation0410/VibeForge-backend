package com.yupi.yuaicodemother.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublicationService;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublishResult;
import com.yupi.yuaicodemother.core.artifact.ArtifactValidationException;
import com.yupi.yuaicodemother.core.artifact.HtmlArtifactValidator;
import com.yupi.yuaicodemother.core.artifact.MultiFileArtifactValidator;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.when;

class InternalAiToolsControllerTest {
    private static final String HTML = "```html\n<!doctype html><html><head></head><body>ready</body></html>\n```";

    /** 验证单文件 HTML 工具请求使用严格解析及校验，截断响应不会被宽松接受。 */
    @Test
    void validatesHtmlAndRejectsIncidentTruncation() {
        var publisher = mock(ArtifactPublicationService.class);
        var controller = controller(publisher);
        var valid = invoke(controller, "artifact_validate", "HTML", HTML);
        assertEquals(true, valid.get("valid"));
        var invalid = invoke(controller, "artifact_validate", "HTML", "以下是代码：\n```html\n<html><body><script>${escapeText");
        assertEquals(false, invalid.get("valid"));
        assertEquals("HTML_FORMAT_INVALID", ((Map<?, ?>) ((java.util.List<?>) invalid.get("errors")).getFirst()).get("code"));
        verifyNoInteractions(publisher);
    }

    /** 相同工具调用只返回首次发布结果；烟测失败必须阻止成功响应。 */
    @Test
    void publishesHtmlIdempotentlyAndPropagatesSmokeFailure() {
        var publisher = mock(ArtifactPublicationService.class);
        var controller = controller(publisher);
        when(publisher.publishHtml(42L, "req-html", HTML, "langgraph", "STOP"))
                .thenReturn(new ArtifactPublishResult(true, "v1", Map.of("index.html", "hash")));
        var arguments = Map.<String, Object>of("appId", 42L, "codeGenType", "HTML", "requestId", "req-html",
                "artifact", HTML, "engine", "langgraph", "finishReason", "STOP");
        String callId = UUID.randomUUID().toString();
        var request = new InternalAiToolsController.ToolRequest(callId, "artifact_publish", arguments);
        assertEquals("v1", controller.invoke("Bearer test-token", request).getData().get("versionId"));
        assertEquals("v1", controller.invoke("Bearer test-token", request).getData().get("versionId"));
        verify(publisher, times(1)).publishHtml(42L, "req-html", HTML, "langgraph", "STOP");

        when(publisher.publishHtml(42L, "req-fail", HTML, "langgraph", "STOP"))
                .thenThrow(new ArtifactValidationException("HTML_SMOKE_TEST_FAILED", "index.html", "脚本错误"));
        var failed = new HashMap<>(arguments);
        failed.put("requestId", "req-fail");
        assertThrows(ArtifactValidationException.class, () -> controller.invoke("Bearer test-token",
                new InternalAiToolsController.ToolRequest(UUID.randomUUID().toString(), "artifact_publish", failed)));
    }

    /** 多文件调用仍委派原发布边界，不受 HTML 分派影响。 */
    @Test
    void keepsMultiFilePublication() {
        var publisher = mock(ArtifactPublicationService.class);
        var controller = controller(publisher);
        when(publisher.publishMultiFile(42L, "req-multi", "candidate", "langgraph", "STOP"))
                .thenReturn(new ArtifactPublishResult(true, "multi-v1", Map.of()));
        var args = Map.<String, Object>of("appId", 42L, "codeGenType", "MULTI_FILE", "requestId", "req-multi",
                "artifact", "candidate", "engine", "langgraph", "finishReason", "STOP");
        assertEquals("multi-v1", controller.invoke("Bearer test-token", new InternalAiToolsController.ToolRequest(
                UUID.randomUUID().toString(), "artifact_publish", args)).getData().get("versionId"));
    }

    private Map<String, Object> invoke(InternalAiToolsController controller, String tool, String type, String artifact) {
        return controller.invoke("Bearer test-token", new InternalAiToolsController.ToolRequest(
                UUID.randomUUID().toString(), tool, Map.of("appId", 42L, "codeGenType", type, "artifact", artifact))).getData();
    }

    private InternalAiToolsController controller(ArtifactPublicationService publisher) {
        var properties = new AiEngineProperties();
        properties.setToken("test-token");
        return new InternalAiToolsController(properties, mock(VueProjectBuilder.class), new ObjectMapper(),
                mock(ArtifactPathResolver.class), publisher, mock(MultiFileArtifactValidator.class), new HtmlArtifactValidator());
    }

    @ParameterizedTest
    @ValueSource(strings = {"file_write", "file_modify", "file_delete"})
    void rejectsMutatingToolsForMultiFileReleases(String toolName) {
        AiEngineProperties properties = new AiEngineProperties();
        properties.setToken("test-token");
        ArtifactPathResolver resolver = mock(ArtifactPathResolver.class);
        var controller = new InternalAiToolsController(
                properties,
                mock(VueProjectBuilder.class),
                new ObjectMapper(),
                resolver,
                mock(ArtifactPublicationService.class),
                mock(MultiFileArtifactValidator.class), new HtmlArtifactValidator());
        Map<String, Object> arguments = new HashMap<>();
        arguments.put("appId", 42L);
        arguments.put("codeGenType", "MULTI_FILE");
        arguments.put("relativeFilePath", "style.css");
        arguments.put("content", "tampered");
        arguments.put("oldContent", "before");
        arguments.put("newContent", "after");

        BusinessException error = assertThrows(BusinessException.class, () -> controller.invoke(
                "Bearer test-token",
                new InternalAiToolsController.ToolRequest(UUID.randomUUID().toString(), toolName, arguments)));

        assertEquals(ErrorCode.PARAMS_ERROR.getCode(), error.getCode());
        assertEquals("MULTI_FILE_RELEASE_IMMUTABLE", error.getMessage());
        verifyNoInteractions(resolver);
    }

    @Test
    void rejectsUnsupportedModelToolName() {
        var controller = controller(mock(ArtifactPublicationService.class));
        var request = new InternalAiToolsController.ToolRequest(
                UUID.randomUUID().toString(),
                "search_reference",
                Map.of("appId", 42L, "q", "layout")
        );

        BusinessException error = assertThrows(BusinessException.class,
                () -> controller.invoke("Bearer test-token", request));

        assertEquals(ErrorCode.PARAMS_ERROR.getCode(), error.getCode());
        assertEquals("Unsupported tool: search_reference", error.getMessage());
    }
}
