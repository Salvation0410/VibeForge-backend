package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.ai.gateway.ToolInvocationIdempotencyService;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.core.artifact.ArtifactContextReader;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublicationService;
import com.yupi.yuaicodemother.core.artifact.HtmlArtifactValidator;
import com.yupi.yuaicodemother.core.artifact.MultiFileArtifactValidator;
import com.yupi.yuaicodemother.core.artifact.VueSourceSnapshotReader;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.GlobalExceptionHandler;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.Mock;
import org.mockito.MockitoAnnotations;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.util.List;
import java.util.Map;

import static org.hamcrest.Matchers.is;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

class InternalAiToolsHttpContractTest {
    private MockMvc mockMvc;

    @Mock
    private VueSourceSnapshotReader snapshotReader;

    @Mock
    private ToolInvocationIdempotencyService idempotencyService;

    @BeforeEach
    void setUp() {
        MockitoAnnotations.openMocks(this);
        var properties = new AiEngineProperties();
        properties.setToken("test-token");
        var controller = new InternalAiToolsController(
                properties,
                org.mockito.Mockito.mock(VueProjectBuilder.class),
                org.mockito.Mockito.mock(ArtifactPathResolver.class),
                org.mockito.Mockito.mock(ArtifactContextReader.class),
                org.mockito.Mockito.mock(ArtifactPublicationService.class),
                org.mockito.Mockito.mock(MultiFileArtifactValidator.class),
                new HtmlArtifactValidator(),
                snapshotReader,
                idempotencyService);
        mockMvc = MockMvcBuilders.standaloneSetup(controller)
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
    }

    @Test
    void returnsVueSourceSnapshotInSpringSuccessEnvelope() throws Exception {
        when(snapshotReader.read(42L)).thenReturn(Map.of(
                "files", List.of(Map.of(
                        "path", "src/App.vue",
                        "content", "<template />",
                        "truncated", false)),
                "eligibleFileCount", 1,
                "includedFileCount", 1,
                "omittedFileCount", 0,
                "truncated", false));

        mockMvc.perform(snapshotRequest("Bearer test-token", "{\"codeGenType\":\"VUE_PROJECT\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code", is(0)))
                .andExpect(jsonPath("$.data.files[0].path", is("src/App.vue")))
                .andExpect(jsonPath("$.data.files[0].truncated", is(false)))
                .andExpect(jsonPath("$.data.eligibleFileCount", is(1)))
                .andExpect(jsonPath("$.data.includedFileCount", is(1)))
                .andExpect(jsonPath("$.data.omittedFileCount", is(0)))
                .andExpect(jsonPath("$.data.truncated", is(false)))
                .andExpect(jsonPath("$.message", is("ok")));

        verify(snapshotReader).read(42L);
        verify(idempotencyService, never()).execute(
                org.mockito.ArgumentMatchers.anyLong(),
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.any(),
                org.mockito.ArgumentMatchers.anyMap(),
                org.mockito.ArgumentMatchers.any());
    }

    @Test
    void rejectsUnauthenticatedSnapshotBeforeReading() throws Exception {
        mockMvc.perform(snapshotRequest("Bearer wrong-token", "{\"codeGenType\":\"VUE_PROJECT\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code", is(ErrorCode.NO_AUTH_ERROR.getCode())))
                .andExpect(jsonPath("$.message", is("Invalid internal bearer token")));

        verify(snapshotReader, never()).read(42L);
    }

    @Test
    void rejectsSnapshotArgumentsOutsideSharedContractBeforeReading() throws Exception {
        mockMvc.perform(snapshotRequest("Bearer test-token",
                        "{\"codeGenType\":\"VUE_PROJECT\",\"relativeFilePath\":\"src/App.vue\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code", is(ErrorCode.PARAMS_ERROR.getCode())))
                .andExpect(jsonPath("$.message", is("vue_source_snapshot accepts only codeGenType")));

        verify(snapshotReader, never()).read(42L);
    }

    @Test
    void rejectsNonVueSnapshotTypeBeforeReading() throws Exception {
        mockMvc.perform(snapshotRequest("Bearer test-token", "{\"codeGenType\":\"HTML\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code", is(ErrorCode.PARAMS_ERROR.getCode())))
                .andExpect(jsonPath("$.message", is("vue_source_snapshot requires VUE_PROJECT")));

        verify(snapshotReader, never()).read(42L);
    }

    private org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder snapshotRequest(
            String authorization,
            String arguments) {
        return post("/internal/ai-tools/invoke")
                .header("Authorization", authorization)
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                        {"appId":42,"requestId":"req-snapshot","toolCallId":"call-snapshot",
                         "toolName":"vue_source_snapshot","arguments":%s}
                        """.formatted(arguments));
    }
}
