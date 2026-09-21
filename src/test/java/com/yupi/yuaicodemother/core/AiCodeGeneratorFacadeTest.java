package com.yupi.yuaicodemother.core;

import com.yupi.yuaicodemother.ai.AiCodeGeneratorService;
import com.yupi.yuaicodemother.ai.AiCodeGeneratorServiceFactory;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublishResult;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublicationService;
import com.yupi.yuaicodemother.core.artifact.ArtifactValidationException;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import dev.langchain4j.agent.tool.ToolExecutionRequest;
import dev.langchain4j.model.chat.response.ChatResponse;
import dev.langchain4j.model.output.FinishReason;
import dev.langchain4j.rag.content.Content;
import dev.langchain4j.service.TokenStream;
import dev.langchain4j.service.tool.ToolExecution;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import java.util.List;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.BiConsumer;
import java.util.function.Consumer;

import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class AiCodeGeneratorFacadeTest {

    private static final long APP_ID = 7L;
    private static final String REQUEST_ID = "request-1";
    private final AiCodeGeneratorServiceFactory factory = mock(AiCodeGeneratorServiceFactory.class);
    private final AiCodeGeneratorService aiService = mock(AiCodeGeneratorService.class);
    private final ArtifactPublicationService publisher = mock(ArtifactPublicationService.class);
    private final AiCodeGeneratorFacade facade = new AiCodeGeneratorFacade();

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(facade, "aiCodeGeneratorServiceFactory", factory);
        ReflectionTestUtils.setField(facade, "artifactPublicationService", publisher);
        when(factory.getAiCodeGeneratorService(APP_ID, CodeGenTypeEnum.HTML)).thenReturn(aiService);
    }

    @Test
    void stopPublishesHtmlBeforeFluxCompletes() {
        FakeTokenStream stream = new FakeTokenStream(validHtml(), FinishReason.STOP);
        when(aiService.generateHtmlCodeStream("build")).thenReturn(stream);
        AtomicBoolean publishedBeforeTermination = new AtomicBoolean();
        doAnswer(invocation -> {
            publishedBeforeTermination.set(true);
            return mock(ArtifactPublishResult.class);
        }).when(publisher).publishHtml(APP_ID, REQUEST_ID, validHtml(), "legacy", "STOP");

        List<String> chunks = facade.generateAndSaveCodeStream("build", CodeGenTypeEnum.HTML, APP_ID, REQUEST_ID)
                .doOnComplete(() -> assertTrue(publishedBeforeTermination.get()))
                .collectList().block();

        assertEquals(List.of(validHtml()), chunks);
        verify(publisher).publishHtml(APP_ID, REQUEST_ID, validHtml(), "legacy", "STOP");
    }

    @Test
    void lengthFinishRejectsWithoutPublishing() {
        when(aiService.generateHtmlCodeStream("build"))
                .thenReturn(new FakeTokenStream(validHtml(), FinishReason.LENGTH));

        ArtifactValidationException error = assertThrows(ArtifactValidationException.class,
                () -> facade.generateAndSaveCodeStream("build", CodeGenTypeEnum.HTML, APP_ID, REQUEST_ID)
                        .collectList().block());

        assertEquals("MODEL_OUTPUT_TRUNCATED", error.getErrorCode());
        verifyNoInteractions(publisher);
    }

    @Test
    void incidentTruncationIsReportedAsPublicationError() {
        String truncated = "```html\n<html><body><script>${escapeText";
        when(aiService.generateHtmlCodeStream("build"))
                .thenReturn(new FakeTokenStream(truncated, FinishReason.STOP));
        when(publisher.publishHtml(APP_ID, REQUEST_ID, truncated, "legacy", "STOP"))
                .thenThrow(new ArtifactValidationException("HTML_FORMAT_INVALID", "index.html", "HTML 输出格式无效"));

        ArtifactValidationException error = assertThrows(ArtifactValidationException.class,
                () -> facade.generateAndSaveCodeStream("build", CodeGenTypeEnum.HTML, APP_ID, REQUEST_ID)
                        .collectList().block());

        assertEquals("HTML_FORMAT_INVALID", error.getErrorCode());
    }

    @Test
    void publishFailureNeverCompletesNormally() {
        when(aiService.generateHtmlCodeStream("build"))
                .thenReturn(new FakeTokenStream(validHtml(), FinishReason.STOP));
        when(publisher.publishHtml(anyLong(), anyString(), anyString(), anyString(), anyString()))
                .thenThrow(new ArtifactValidationException("HTML_SMOKE_TEST_FAILED", "index.html", "脚本错误"));

        ArtifactValidationException error = assertThrows(ArtifactValidationException.class,
                () -> facade.generateAndSaveCodeStream("build", CodeGenTypeEnum.HTML, APP_ID, REQUEST_ID)
                        .collectList().block());

        assertEquals("HTML_SMOKE_TEST_FAILED", error.getErrorCode());
    }

    @Test
    void cancellationBeforeCompletionPreventsPublication() {
        when(aiService.generateHtmlCodeStream("build"))
                .thenReturn(new FakeTokenStream(validHtml(), FinishReason.STOP));
        facade.cancelGeneration(REQUEST_ID);

        assertThrows(java.util.concurrent.CancellationException.class,
                () -> facade.generateAndSaveCodeStream("build", CodeGenTypeEnum.HTML, APP_ID, REQUEST_ID)
                        .collectList().block());

        verifyNoInteractions(publisher);
    }

    private String validHtml() {
        return "```html\n<!doctype html><html><head></head><body>OK</body></html>\n```";
    }

    /** 可控地同步回放分片和完成回调，避免测试依赖真实模型与线程调度。 */
    private static final class FakeTokenStream implements TokenStream {
        private final String chunk;
        private final FinishReason finishReason;
        private Consumer<String> partialHandler;
        private Consumer<ChatResponse> completeHandler;
        private Consumer<Throwable> errorHandler;

        private FakeTokenStream(String chunk, FinishReason finishReason) {
            this.chunk = chunk;
            this.finishReason = finishReason;
        }

        @Override public TokenStream onPartialResponse(Consumer<String> handler) { partialHandler = handler; return this; }
        @Override public TokenStream onPartialToolExecutionRequest(BiConsumer<Integer, ToolExecutionRequest> handler) { return this; }
        @Override public TokenStream onCompleteToolExecutionRequest(BiConsumer<Integer, ToolExecutionRequest> handler) { return this; }
        @Override public TokenStream onRetrieved(Consumer<List<Content>> handler) { return this; }
        @Override public TokenStream onToolExecuted(Consumer<ToolExecution> handler) { return this; }
        @Override public TokenStream onCompleteResponse(Consumer<ChatResponse> handler) { completeHandler = handler; return this; }
        @Override public TokenStream onError(Consumer<Throwable> handler) { errorHandler = handler; return this; }
        @Override public TokenStream ignoreErrors() { errorHandler = null; return this; }

        @Override
        public void start() {
            try {
                partialHandler.accept(chunk);
                ChatResponse response = mock(ChatResponse.class);
                when(response.finishReason()).thenReturn(finishReason);
                completeHandler.accept(response);
            } catch (Throwable error) {
                if (errorHandler != null) errorHandler.accept(error);
                else throw error;
            }
        }
    }
}
