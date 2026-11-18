package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.model.dto.customerservice.CustomerServiceAskRequest;
import com.yupi.yuaicodemother.model.entity.CustomerServiceKnowledgeDocument;
import com.yupi.yuaicodemother.model.vo.CustomerServiceAnswerVO;
import com.yupi.yuaicodemother.service.impl.CustomerServiceAnswerServiceImpl;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.inOrder;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class CustomerServiceMultiChunkSourcesTest {
    private static final String ANSWER = "按使用说明操作，并参考另一份文档。";
    private static final CustomerServiceAiClient.AnswerSource FIRST = source(
            "7", "使用说明", 2, "7:2:1", "第 2 节 / 操作步骤", "先打开设置，再选择当前应用。");
    private static final CustomerServiceAiClient.AnswerSource SAME_DOCUMENT = source(
            "7", "使用说明", 2, "7:2:0", "第 1 节 / 使用前提", "请先登录已获得授权的账号。");
    private static final CustomerServiceAiClient.AnswerSource OTHER_DOCUMENT = source(
            "8", "常见问题", 3, "8:3:0", "常见问题 / 补充说明", "只有应用所有者可以继续操作。");

    @Test
    void preservesMultipleChunksAndAnotherDocumentInWireOrder() throws Exception {
        var mapper = documentMapper(activeDocument(8, "常见问题", 3, 1));
        try (var http = new AnswerHttpFixture(List.of(FIRST, SAME_DOCUMENT, OTHER_DOCUMENT))) {
            var result = http.service(mapper).answer(question(), 1L);

            assertTrue(result.isAnswered());
            assertEquals(ANSWER, result.getAnswer());
            // 倒序块号必须保持供应方顺序，同文档的两块不能被按文档去重。
            assertEquals(List.of(
                    new CustomerServiceAnswerVO.Source("7", "使用说明", 2, "7:2:1",
                            "第 2 节 / 操作步骤", "先打开设置，再选择当前应用。"),
                    new CustomerServiceAnswerVO.Source("7", "使用说明", 2, "7:2:0",
                            "第 1 节 / 使用前提", "请先登录已获得授权的账号。"),
                    new CustomerServiceAnswerVO.Source("8", "常见问题", 3, "8:3:0",
                            "常见问题 / 补充说明", "只有应用所有者可以继续操作。")), result.getSources());
            var reads = inOrder(mapper);
            reads.verify(mapper, times(2)).findIncludingDeleted(7L);
            reads.verify(mapper).findIncludingDeleted(8L);
            reads.verifyNoMoreInteractions();
            assertEquals(1, http.requests.size());
            assertFalse(http.requests.getFirst().requestId().isBlank());
            assertEquals("如何操作？", http.requests.getFirst().question());
        }
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("invalidSecondSources")
    void rejectsWholeAnswerAfterAValidFirstSource(String scenario,
                                                 CustomerServiceAiClient.AnswerSource second,
                                                 CustomerServiceKnowledgeDocument secondDocument) throws Exception {
        var mapper = documentMapper(secondDocument);
        var last = "7".equals(second.documentId()) ? OTHER_DOCUMENT : SAME_DOCUMENT;
        try (var http = new AnswerHttpFixture(List.of(FIRST, second, last))) {
            var returned = new AtomicReference<CustomerServiceAnswerVO>();
            var error = assertThrows(BusinessException.class,
                    () -> returned.set(http.service(mapper).answer(question(), 1L)), scenario);

            assertUnavailable(error);
            assertNull(returned.get(), "来源无效时不能返回已累积的部分来源");
            // 同文档仅破坏第二块的来源身份；文档状态异常放在另一文档，避免首个来源提前失败。
            var reads = inOrder(mapper);
            if ("7".equals(second.documentId())) {
                reads.verify(mapper, times(2)).findIncludingDeleted(7L);
            } else {
                reads.verify(mapper).findIncludingDeleted(7L);
                reads.verify(mapper).findIncludingDeleted(8L);
            }
            reads.verifyNoMoreInteractions();
            assertEquals(1, http.requests.size());
        }
    }

    static Stream<Arguments> invalidSecondSources() {
        var inactive = activeDocument(8, "常见问题", 3, 1);
        inactive.setStatus("DISABLED");
        var deleted = activeDocument(8, "常见问题", 3, 1);
        deleted.setIsDelete(1);
        var staleIndex = activeDocument(8, "常见问题", 3, 1);
        staleIndex.setIndexedVersion(2L);
        return Stream.of(
                Arguments.of("重复 chunk 整次拒绝", FIRST, activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("同文档第二块来源版本过旧", source("7", "使用说明", 1, "7:1:0", "旧版位置", "旧版摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("同文档第二块文档名不符", source("7", "另一文档", 2, "7:2:0", "位置", "摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("同文档第二块的文档身份不符", source("7", "使用说明", 2, "8:2:0", "位置", "摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("同文档第二块的版本身份不符", source("7", "使用说明", 2, "7:1:0", "位置", "摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("同文档第二块索引越界", source("7", "使用说明", 2, "7:2:2", "位置", "摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("第二来源版本过旧", source("8", "常见问题", 2, "8:2:0", "旧版位置", "旧版摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("第二来源文档名不符", source("8", "另一文档", 3, "8:3:0", "位置", "摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("第二来源块的文档身份不符", source("8", "常见问题", 3, "7:3:0", "位置", "摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("第二来源块的版本身份不符", source("8", "常见问题", 3, "8:2:0", "位置", "摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("第二来源块索引越界", source("8", "常见问题", 3, "8:3:1", "位置", "摘录"),
                        activeDocument(8, "常见问题", 3, 1)),
                Arguments.of("第二来源文档非活动", OTHER_DOCUMENT, inactive),
                Arguments.of("第二来源文档已删除", OTHER_DOCUMENT, deleted),
                Arguments.of("第二来源索引版本过旧", OTHER_DOCUMENT, staleIndex));
    }

    @Test
    void rejectsMoreThanThreeSourcesAtHttpClientBoundaryBeforeDocumentLookup() throws Exception {
        var fourth = source("8", "常见问题", 3, "8:3:1", "第二块", "第四条来源。");
        var mapper = mock(CustomerServiceKnowledgeDocumentMapper.class);
        try (var http = new AnswerHttpFixture(List.of(FIRST, SAME_DOCUMENT, OTHER_DOCUMENT, fourth))) {
            var clientError = assertThrows(CustomerServiceAiClient.CallException.class,
                    () -> http.client.answer(new CustomerServiceAiClient.AnswerRequest("direct-request", "如何操作？")));
            assertEquals("KNOWLEDGE_AI_RESPONSE_INVALID", clientError.code());
            assertFalse(clientError.transientFailure());

            var serviceError = assertThrows(BusinessException.class,
                    () -> http.service(mapper).answer(question(), 1L));
            assertUnavailable(serviceError);
            verifyNoInteractions(mapper);
            assertEquals(2, http.requests.size());
            assertEquals("direct-request", http.requests.getFirst().requestId());
            assertFalse(http.requests.getLast().requestId().isBlank());
        }
    }

    private static void assertUnavailable(BusinessException error) {
        assertEquals(ErrorCode.SYSTEM_ERROR.getCode(), error.getCode());
        assertEquals("客服服务暂不可用", error.getMessage());
    }

    private static CustomerServiceAskRequest question() {
        var request = new CustomerServiceAskRequest();
        request.setQuestion("  如何操作？  ");
        return request;
    }

    private static CustomerServiceAiClient.AnswerSource source(String documentId, String documentName,
                                                               long version, String chunkId,
                                                               String locator, String excerpt) {
        return new CustomerServiceAiClient.AnswerSource(documentId, documentName, version, chunkId, locator, excerpt);
    }

    private static CustomerServiceKnowledgeDocument activeDocument(long id, String name, long version, int chunks) {
        return CustomerServiceKnowledgeDocument.builder().id(id).name(name).documentVersion(version)
                .indexedVersion(version).chunkCount(chunks).status("ACTIVE").isDelete(0).build();
    }

    private static CustomerServiceKnowledgeDocumentMapper documentMapper(CustomerServiceKnowledgeDocument second) {
        var mapper = mock(CustomerServiceKnowledgeDocumentMapper.class);
        when(mapper.findIncludingDeleted(7L)).thenReturn(activeDocument(7, "使用说明", 2, 2));
        when(mapper.findIncludingDeleted(8L)).thenReturn(second);
        return mapper;
    }

    private static final class AnswerHttpFixture implements AutoCloseable {
        private final HttpServer server;
        private final ObjectMapper json = new ObjectMapper();
        private final AtomicReference<Throwable> handlerFailure = new AtomicReference<>();
        private final List<CustomerServiceAiClient.AnswerRequest> requests = new CopyOnWriteArrayList<>();
        private final CustomerServiceProperties properties = new CustomerServiceProperties();
        private final CustomerServiceAiClient client;

        private AnswerHttpFixture(List<CustomerServiceAiClient.AnswerSource> sources) throws IOException {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            try {
                server.createContext("/internal/v1/customer-service/answers", exchange -> respond(exchange, sources));
                server.start();
                properties.setEnabled(true);
                properties.setServiceUrl("http://127.0.0.1:" + server.getAddress().getPort());
                var ai = new AiEngineProperties();
                ai.setToken("local-test-token");
                client = new CustomerServiceAiClient(properties, ai);
            } catch (RuntimeException | Error failure) {
                server.stop(0);
                throw failure;
            }
        }

        private CustomerServiceAnswerServiceImpl service(CustomerServiceKnowledgeDocumentMapper mapper) {
            return new CustomerServiceAnswerServiceImpl(client, mapper, properties);
        }

        private void respond(HttpExchange exchange, List<CustomerServiceAiClient.AnswerSource> sources) throws IOException {
            try {
                var request = json.readValue(exchange.getRequestBody(), CustomerServiceAiClient.AnswerRequest.class);
                requests.add(request);
                // Service 每次生成新的 requestId，必须回显真实请求，不能固定返回绕过关联校验。
                byte[] response = json.writeValueAsBytes(Map.of("requestId", request.requestId(),
                        "answered", true, "answer", ANSWER, "sources", sources, "degraded", false));
                exchange.getResponseHeaders().add("Content-Type", "application/json");
                exchange.sendResponseHeaders(200, response.length);
                exchange.getResponseBody().write(response);
            } catch (IOException | RuntimeException | Error failure) {
                handlerFailure.compareAndSet(null, failure);
                throw failure;
            } finally {
                exchange.close();
            }
        }

        @Override
        public void close() {
            server.stop(0);
            // HTTP 回调在独立线程执行；把异常送回测试线程，避免错误响应碰巧满足拒绝断言。
            Throwable failure = handlerFailure.get();
            if (failure != null) throw new AssertionError("本地 HTTP 替身处理失败", failure);
        }
    }
}
