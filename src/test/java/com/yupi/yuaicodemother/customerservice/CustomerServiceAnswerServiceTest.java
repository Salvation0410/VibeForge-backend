package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.model.dto.customerservice.CustomerServiceAskRequest;
import com.yupi.yuaicodemother.service.impl.CustomerServiceAnswerServiceImpl;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.*;
import static org.junit.jupiter.api.Assertions.assertEquals;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import java.util.List;

class CustomerServiceAnswerServiceTest {
    @Test
    void disabledDoesNotCallPython() {
        CustomerServiceAiClient client = mock(CustomerServiceAiClient.class);
        CustomerServiceProperties properties = new CustomerServiceProperties();
        CustomerServiceAnswerServiceImpl service = new CustomerServiceAnswerServiceImpl(client,
                mock(CustomerServiceKnowledgeDocumentMapper.class), properties);
        CustomerServiceAskRequest request = new CustomerServiceAskRequest();
        request.setQuestion("hello");
        assertThrows(RuntimeException.class, () -> service.answer(request, 1L));
        verifyNoInteractions(client);
    }

    @Test
    void rejectsBlankAndUtf8OversizeBeforePython() {
        CustomerServiceAiClient client = mock(CustomerServiceAiClient.class);
        CustomerServiceProperties properties = new CustomerServiceProperties();
        properties.setEnabled(true);
        CustomerServiceAnswerServiceImpl service = new CustomerServiceAnswerServiceImpl(client,
                mock(CustomerServiceKnowledgeDocumentMapper.class), properties);
        CustomerServiceAskRequest blank = new CustomerServiceAskRequest();
        blank.setQuestion("   ");
        assertThrows(RuntimeException.class, () -> service.answer(blank, 1L));
        CustomerServiceAskRequest large = new CustomerServiceAskRequest();
        large.setQuestion("界".repeat(6_000));
        assertThrows(RuntimeException.class, () -> service.answer(large, 1L));
        verifyNoInteractions(client);
    }

    @Test
    void rejectsDuplicateSources() {
        CustomerServiceAiClient client = mock(CustomerServiceAiClient.class);
        CustomerServiceProperties properties = new CustomerServiceProperties(); properties.setEnabled(true);
        CustomerServiceKnowledgeDocumentMapper mapper = mock(CustomerServiceKnowledgeDocumentMapper.class);
        CustomerServiceKnowledgeDocument doc = CustomerServiceKnowledgeDocument.builder().id(7L).name("guide")
                .documentVersion(2L).indexedVersion(2L).chunkCount(2).status("READY").isDelete(0).build();
        when(mapper.findIncludingDeleted(7L)).thenReturn(doc);
        CustomerServiceAnswerServiceImpl service = new CustomerServiceAnswerServiceImpl(client, mapper, properties);
        CustomerServiceAskRequest request = new CustomerServiceAskRequest(); request.setQuestion("hello");
        CustomerServiceAiClient.AnswerSource source = new CustomerServiceAiClient.AnswerSource("7", "guide", 2,
                "7:2:0", "section", "excerpt");
        when(client.answer(any())).thenAnswer(invocation -> {
            CustomerServiceAiClient.AnswerRequest sent = invocation.getArgument(0);
            return new CustomerServiceAiClient.AnswerResponse(
                    sent.requestId(), true, "answer", List.of(source, source));
        });
        assertThrows(RuntimeException.class, () -> service.answer(request, 1L));
    }

    @Test
    void rejectsSourceWithStaleDocumentVersion() {
        assertSourceRejected(CustomerServiceKnowledgeDocument.builder().id(7L).name("guide")
                        .documentVersion(2L).indexedVersion(2L).chunkCount(2).status("READY").isDelete(0).build(),
                new CustomerServiceAiClient.AnswerSource("7", "guide", 1,
                        "7:1:0", "section", "excerpt"));
    }

    @Test
    void rejectsSourceWhenIndexedVersionIsStale() {
        assertSourceRejected(CustomerServiceKnowledgeDocument.builder().id(7L).name("guide")
                        .documentVersion(2L).indexedVersion(1L).chunkCount(2).status("READY").isDelete(0).build(),
                new CustomerServiceAiClient.AnswerSource("7", "guide", 2,
                        "7:2:0", "section", "excerpt"));
    }

    @Test
    void rejectsChunkIdWithNumericComponentBeyondLongRange() {
        assertSourceRejected(CustomerServiceKnowledgeDocument.builder().id(7L).name("guide")
                        .documentVersion(2L).indexedVersion(2L).chunkCount(2).status("READY").isDelete(0).build(),
                new CustomerServiceAiClient.AnswerSource("7", "guide", 2,
                        "7:2:999999999999999999999999999", "section", "excerpt"));
    }

    private void assertSourceRejected(CustomerServiceKnowledgeDocument document,
                                      CustomerServiceAiClient.AnswerSource source) {
        CustomerServiceAiClient client = mock(CustomerServiceAiClient.class);
        CustomerServiceProperties properties = new CustomerServiceProperties(); properties.setEnabled(true);
        CustomerServiceKnowledgeDocumentMapper mapper = mock(CustomerServiceKnowledgeDocumentMapper.class);
        when(mapper.findIncludingDeleted(7L)).thenReturn(document);
        when(client.answer(any())).thenAnswer(invocation -> {
            CustomerServiceAiClient.AnswerRequest sent = invocation.getArgument(0);
            return new CustomerServiceAiClient.AnswerResponse(sent.requestId(), true, "answer", List.of(source));
        });
        CustomerServiceAnswerServiceImpl service = new CustomerServiceAnswerServiceImpl(client, mapper, properties);
        CustomerServiceAskRequest request = new CustomerServiceAskRequest(); request.setQuestion("hello");
        assertThrows(com.yupi.yuaicodemother.exception.BusinessException.class, () -> service.answer(request, 1L));
    }
}
