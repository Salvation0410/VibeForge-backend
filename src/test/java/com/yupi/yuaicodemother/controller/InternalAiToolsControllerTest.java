package com.yupi.yuaicodemother.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.ai.gateway.InternalAiTool;
import com.yupi.yuaicodemother.ai.gateway.ToolInvocationIdempotencyService;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublicationService;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublishResult;
import com.yupi.yuaicodemother.core.artifact.ArtifactValidationException;
import com.yupi.yuaicodemother.core.artifact.HtmlArtifactValidator;
import com.yupi.yuaicodemother.core.artifact.MultiFileArtifactValidator;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.lang.reflect.Modifier;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyMap;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class InternalAiToolsControllerTest {
    private static final String HTML = "```html\n<!doctype html><html><head></head><body>ready</body></html>\n```";

    @TempDir
    Path tempDir;

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

    @Test
    void publishesHtmlAndPropagatesSmokeFailure() {
        var publisher = mock(ArtifactPublicationService.class);
        var controller = controller(publisher);
        when(publisher.publishHtml(42L, "req-html", HTML, "langgraph", "STOP"))
                .thenReturn(new ArtifactPublishResult(true, "v1", Map.of("index.html", "hash")));
        var arguments = Map.<String, Object>of("codeGenType", "HTML", "artifact", HTML,
                "engine", "langgraph", "finishReason", "STOP");
        var request = new InternalAiToolsController.ToolRequest(42L, "req-html", "call-html",
                "artifact_publish", arguments);
        assertEquals("v1", controller.invoke("Bearer test-token", request).getData().get("versionId"));
        verify(publisher).publishHtml(42L, "req-html", HTML, "langgraph", "STOP");

        when(publisher.publishHtml(42L, "req-fail", HTML, "langgraph", "STOP"))
                .thenThrow(new ArtifactValidationException("HTML_SMOKE_TEST_FAILED", "index.html", "脚本错误"));
        assertThrows(ArtifactValidationException.class, () -> controller.invoke("Bearer test-token",
                new InternalAiToolsController.ToolRequest(42L, "req-fail", "call-fail",
                        "artifact_publish", arguments)));
    }

    @Test
    void publishesArtifactWithTopLevelRequestId() {
        var publisher = mock(ArtifactPublicationService.class);
        var controller = controller(publisher);
        when(publisher.publishHtml(42L, "req-top", HTML, "langgraph", "STOP"))
                .thenReturn(new ArtifactPublishResult(true, "v1", Map.of()));
        Map<String, Object> arguments = Map.of(
                "requestId", "req-argument", "codeGenType", "HTML", "artifact", HTML,
                "engine", "langgraph", "finishReason", "STOP");

        controller.invoke("Bearer test-token", new InternalAiToolsController.ToolRequest(
                42L, "req-top", "call-publish", "artifact_publish", arguments));

        verify(publisher).publishHtml(42L, "req-top", HTML, "langgraph", "STOP");
    }

    @Test
    void keepsMultiFilePublication() {
        var publisher = mock(ArtifactPublicationService.class);
        var controller = controller(publisher);
        when(publisher.publishMultiFile(42L, "req-multi", "candidate", "langgraph", "STOP"))
                .thenReturn(new ArtifactPublishResult(true, "multi-v1", Map.of()));
        var args = Map.<String, Object>of("codeGenType", "MULTI_FILE", "artifact", "candidate",
                "engine", "langgraph", "finishReason", "STOP");
        assertEquals("multi-v1", controller.invoke("Bearer test-token", new InternalAiToolsController.ToolRequest(
                42L, "req-multi", "call-multi", "artifact_publish", args)).getData().get("versionId"));
    }

    @Test
    void rejectsBlankRequestIdBeforeIdempotencyServiceAndAction() {
        var idempotencyService = mock(ToolInvocationIdempotencyService.class);
        var publisher = mock(ArtifactPublicationService.class);
        var controller = controller(publisher, mock(ArtifactPathResolver.class), idempotencyService);

        BusinessException error = assertThrows(BusinessException.class, () -> controller.invoke(
                "Bearer test-token",
                new InternalAiToolsController.ToolRequest(42L, " ", "call-1", "artifact_publish", Map.of())));

        assertEquals(ErrorCode.PARAMS_ERROR.getCode(), error.getCode());
        verifyNoInteractions(idempotencyService, publisher);
    }

    @Test
    void rejectsJsonRequestWithoutAppIdBeforeIdempotencyAndSandbox() throws Exception {
        var idempotencyService = mock(ToolInvocationIdempotencyService.class);
        var resolver = mock(ArtifactPathResolver.class);
        var controller = controller(mock(ArtifactPublicationService.class), resolver, idempotencyService);
        var request = new ObjectMapper().readValue("""
                {
                  "requestId": "req-1",
                  "toolCallId": "call-1",
                  "toolName": "file_read",
                  "arguments": {"relativeFilePath": "index.html"}
                }
                """, InternalAiToolsController.ToolRequest.class);
        assertEquals(0L, request.appId());

        BusinessException error = assertThrows(BusinessException.class,
                () -> controller.invoke("Bearer test-token", request));

        assertEquals(ErrorCode.PARAMS_ERROR.getCode(), error.getCode());
        assertEquals("appId is required", error.getMessage());
        verifyNoInteractions(idempotencyService, resolver);
    }

    @ParameterizedTest
    @ValueSource(longs = {0L, -1L, -42L})
    void rejectsNonPositiveAppIdBeforeIdempotencyAndSandbox(long appId) {
        var idempotencyService = mock(ToolInvocationIdempotencyService.class);
        var resolver = mock(ArtifactPathResolver.class);
        var controller = controller(mock(ArtifactPublicationService.class), resolver, idempotencyService);
        var request = new InternalAiToolsController.ToolRequest(
                appId, "req-1", "call-1", "file_read", Map.of("relativeFilePath", "index.html"));

        BusinessException error = assertThrows(BusinessException.class,
                () -> controller.invoke("Bearer test-token", request));

        assertEquals(ErrorCode.PARAMS_ERROR.getCode(), error.getCode());
        assertEquals("appId is required", error.getMessage());
        verifyNoInteractions(idempotencyService, resolver);
    }

    @Test
    void usesTopLevelAppIdForIdempotencyAndSandbox() throws Exception {
        var resolver = mock(ArtifactPathResolver.class);
        var idempotencyService = passThroughIdempotencyService();
        Files.writeString(tempDir.resolve("index.html"), "ready");
        when(resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 42L)).thenReturn(tempDir);
        var controller = controller(mock(ArtifactPublicationService.class), resolver, idempotencyService);
        Map<String, Object> arguments = Map.of(
                "appId", 999L, "codeGenType", "HTML", "relativeFilePath", "index.html");

        var result = controller.invoke("Bearer test-token", new InternalAiToolsController.ToolRequest(
                42L, "req-1", "call-1", "file_read", arguments)).getData();

        assertEquals("ready", result.get("content"));
        verify(idempotencyService).execute(eq(42L), eq("req-1"), eq("call-1"), eq(InternalAiTool.FILE_READ),
                eq(arguments), any(ToolInvocationIdempotencyService.ToolAction.class));
        verify(resolver).resolveActiveRoot(CodeGenTypeEnum.HTML, 42L);
    }

    @Test
    void canonicalizesToolAliasBeforeIdempotencyService() {
        var idempotencyService = mock(ToolInvocationIdempotencyService.class);
        when(idempotencyService.execute(anyLong(), anyString(), anyString(), any(), anyMap(), any()))
                .thenReturn(Map.of("content", "cached"));
        var controller = controller(mock(ArtifactPublicationService.class),
                mock(ArtifactPathResolver.class), idempotencyService);
        Map<String, Object> arguments = Map.of("relativeFilePath", "index.html");

        controller.invoke("Bearer test-token", new InternalAiToolsController.ToolRequest(
                42L, "req-1", "call-1", "readFile", arguments));

        verify(idempotencyService).execute(eq(42L), eq("req-1"), eq("call-1"), eq(InternalAiTool.FILE_READ),
                eq(arguments), any(ToolInvocationIdempotencyService.ToolAction.class));
    }

    @Test
    void hasNoStaticMapAndSecondControllerUsesIdempotencyService() {
        boolean hasStaticMap = java.util.Arrays.stream(InternalAiToolsController.class.getDeclaredFields())
                .anyMatch(field -> Modifier.isStatic(field.getModifiers())
                        && Map.class.isAssignableFrom(field.getType()));
        assertFalse(hasStaticMap);

        var firstService = mock(ToolInvocationIdempotencyService.class);
        var secondService = mock(ToolInvocationIdempotencyService.class);
        when(firstService.execute(anyLong(), anyString(), anyString(), any(), anyMap(), any()))
                .thenReturn(Map.of("valid", true));
        when(secondService.execute(anyLong(), anyString(), anyString(), any(), anyMap(), any()))
                .thenReturn(Map.of("valid", true));
        var request = new InternalAiToolsController.ToolRequest(
                42L, "req-shared", "call-shared", "artifact_validate", Map.of("artifact", "candidate"));

        controller(mock(ArtifactPublicationService.class), mock(ArtifactPathResolver.class), firstService)
                .invoke("Bearer test-token", request);
        controller(mock(ArtifactPublicationService.class), mock(ArtifactPathResolver.class), secondService)
                .invoke("Bearer test-token", request);

        verify(firstService).execute(eq(42L), eq("req-shared"), eq("call-shared"),
                eq(InternalAiTool.ARTIFACT_VALIDATE), anyMap(), any(ToolInvocationIdempotencyService.ToolAction.class));
        verify(secondService).execute(eq(42L), eq("req-shared"), eq("call-shared"),
                eq(InternalAiTool.ARTIFACT_VALIDATE), anyMap(), any(ToolInvocationIdempotencyService.ToolAction.class));
    }

    private Map<String, Object> invoke(InternalAiToolsController controller, String tool, String type, String artifact) {
        return controller.invoke("Bearer test-token", new InternalAiToolsController.ToolRequest(
                42L, "req-" + UUID.randomUUID(), UUID.randomUUID().toString(), tool,
                Map.of("codeGenType", type, "artifact", artifact))).getData();
    }

    private InternalAiToolsController controller(ArtifactPublicationService publisher) {
        return controller(publisher, mock(ArtifactPathResolver.class), passThroughIdempotencyService());
    }

    private InternalAiToolsController controller(
            ArtifactPublicationService publisher,
            ArtifactPathResolver resolver,
            ToolInvocationIdempotencyService idempotencyService) {
        var properties = new AiEngineProperties();
        properties.setToken("test-token");
        return new InternalAiToolsController(properties, mock(VueProjectBuilder.class), resolver, publisher,
                mock(MultiFileArtifactValidator.class), new HtmlArtifactValidator(), idempotencyService);
    }

    private ToolInvocationIdempotencyService passThroughIdempotencyService() {
        var service = mock(ToolInvocationIdempotencyService.class);
        when(service.execute(anyLong(), anyString(), anyString(), any(), anyMap(), any()))
                .thenAnswer(invocation -> invocation.<ToolInvocationIdempotencyService.ToolAction>getArgument(5).execute());
        return service;
    }

    @ParameterizedTest
    @ValueSource(strings = {"file_write", "file_modify", "file_delete"})
    void rejectsMutatingToolsForMultiFileReleases(String toolName) {
        ArtifactPathResolver resolver = mock(ArtifactPathResolver.class);
        var controller = controller(mock(ArtifactPublicationService.class), resolver, passThroughIdempotencyService());
        Map<String, Object> arguments = new HashMap<>();
        arguments.put("codeGenType", "MULTI_FILE");
        arguments.put("relativeFilePath", "style.css");
        arguments.put("content", "tampered");
        arguments.put("oldContent", "before");
        arguments.put("newContent", "after");

        BusinessException error = assertThrows(BusinessException.class, () -> controller.invoke(
                "Bearer test-token",
                new InternalAiToolsController.ToolRequest(42L, "req-1", UUID.randomUUID().toString(),
                        toolName, arguments)));

        assertEquals(ErrorCode.PARAMS_ERROR.getCode(), error.getCode());
        assertEquals("MULTI_FILE_RELEASE_IMMUTABLE", error.getMessage());
        verifyNoInteractions(resolver);
    }

    @Test
    void rejectsUnsupportedModelToolName() {
        var idempotencyService = mock(ToolInvocationIdempotencyService.class);
        var controller = controller(mock(ArtifactPublicationService.class),
                mock(ArtifactPathResolver.class), idempotencyService);
        var request = new InternalAiToolsController.ToolRequest(
                42L, "req-1", UUID.randomUUID().toString(), "search_reference", Map.of("q", "layout"));

        BusinessException error = assertThrows(BusinessException.class,
                () -> controller.invoke("Bearer test-token", request));

        assertEquals(ErrorCode.PARAMS_ERROR.getCode(), error.getCode());
        assertEquals("Unsupported tool: search_reference", error.getMessage());
        verifyNoInteractions(idempotencyService);
    }
}
