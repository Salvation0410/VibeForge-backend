package com.yupi.yuaicodemother.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublicationService;
import com.yupi.yuaicodemother.core.artifact.MultiFileArtifactValidator;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.util.HashMap;
import java.util.Map;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;

class InternalAiToolsControllerTest {

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
                mock(MultiFileArtifactValidator.class));
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
}
