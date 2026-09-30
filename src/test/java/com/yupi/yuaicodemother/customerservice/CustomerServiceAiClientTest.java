package com.yupi.yuaicodemother.customerservice;

import com.sun.net.httpserver.HttpServer;
import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class CustomerServiceAiClientTest {
    @Test
    void sendsCamelCaseLeaseAndDoesNotLeakResponseBody() throws Exception {
        var body = new AtomicReference<String>();
        var server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/internal/v1/customer-service/knowledge:etl", exchange -> {
            body.set(new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8));
            byte[] response = "{\"error\":{\"code\":\"BAD_FILE\",\"message\":\"signed-secret\"}}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(400, response.length);
            exchange.getResponseBody().write(response);
            exchange.close();
        });
        server.start();
        try {
            var props = new CustomerServiceProperties();
            props.setServiceUrl("http://localhost:" + server.getAddress().getPort());
            var ai = new AiEngineProperties();
            ai.setToken("token");
            var client = new CustomerServiceAiClient(props, ai);
            var lease = new KnowledgeMutationCoordinator.Lease("document:1", "op_1", "INDEX", 3, 2000000000L, "proof-secret");
            var error = assertThrows(CustomerServiceAiClient.CallException.class, () -> client.index(
                    new CustomerServiceAiClient.IndexRequest("1", 1, "a.txt", "TXT", "https://example.com/signed-secret", "a".repeat(64), "1", lease)));
            assertEquals("BAD_FILE", error.code());
            assertFalse(error.getMessage().contains("signed-secret"));
            assertTrue(body.get().contains("\"operationId\":\"op_1\""));
            assertTrue(body.get().contains("\"operation\":\"INDEX\""));
            assertTrue(body.get().contains("\"expiresAt\":2000000000"));
        } finally {
            server.stop(0);
        }
    }

    @Test
    void rejectsOversizedResponseWithStableError() throws Exception {
        var server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/internal/v1/customer-service/knowledge:delete", exchange -> {
            byte[] response = "x".repeat(100).getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(500, response.length);
            exchange.getResponseBody().write(response); exchange.close();
        });
        server.start();
        try {
            var props = new CustomerServiceProperties();
            props.setServiceUrl("http://localhost:" + server.getAddress().getPort()); props.setMaxResponseBytes(16);
            var ai = new AiEngineProperties(); ai.setToken("token");
            var lease = new KnowledgeMutationCoordinator.Lease("document:1", "op_1", "DELETE", 3, 2000000000L, "proof");
            var error = assertThrows(CustomerServiceAiClient.CallException.class,
                    () -> new CustomerServiceAiClient(props, ai).delete(new CustomerServiceAiClient.DeleteRequest("1", 1, lease)));
            assertEquals("KNOWLEDGE_AI_RESPONSE_TOO_LARGE", error.code());
            assertFalse(error.getMessage().contains("xxxx"));
        } finally { server.stop(0); }
    }

    @Test
    void timesOutWhenHeadersAreFastButBodyIsSlow() throws Exception {
        var server = HttpServer.create(new InetSocketAddress(0), 0);
        server.setExecutor(command -> Thread.startVirtualThread(command));
        server.createContext("/internal/v1/customer-service/knowledge:delete", exchange -> {
            try {
                exchange.getRequestBody().readAllBytes();
                exchange.sendResponseHeaders(200, 0);
                exchange.getResponseBody().write('{');
                exchange.getResponseBody().flush();
                Thread.sleep(5000);
                exchange.getResponseBody().write('}');
            } catch (Exception ignored) {
            } finally {
                exchange.close();
            }
        });
        server.start();
        try {
            var error = assertTimeoutPreemptively(Duration.ofSeconds(3),
                    () -> assertThrows(CustomerServiceAiClient.CallException.class,
                            () -> timeoutClient(server).delete(deleteRequest())));
            assertEquals("KNOWLEDGE_AI_TIMEOUT", error.code());
        } finally { server.stop(0); }
    }

    @Test
    void timesOutAndCancelsBodyThatNeverCompletes() throws Exception {
        var release = new CountDownLatch(1);
        var server = HttpServer.create(new InetSocketAddress(0), 0);
        server.setExecutor(command -> Thread.startVirtualThread(command));
        server.createContext("/internal/v1/customer-service/knowledge:delete", exchange -> {
            try {
                exchange.getRequestBody().readAllBytes();
                exchange.sendResponseHeaders(200, 0);
                exchange.getResponseBody().write("{\"secret\":".getBytes(StandardCharsets.UTF_8));
                exchange.getResponseBody().flush();
                release.await();
            } catch (Exception ignored) {
            } finally {
                exchange.close();
            }
        });
        server.start();
        try {
            var error = assertTimeoutPreemptively(Duration.ofSeconds(3),
                    () -> assertThrows(CustomerServiceAiClient.CallException.class,
                            () -> timeoutClient(server).delete(deleteRequest())));
            assertEquals("KNOWLEDGE_AI_TIMEOUT", error.code());
            assertFalse(error.getMessage().contains("secret"));
        } finally {
            release.countDown();
            server.stop(0);
        }
    }

    @Test
    void cancelsOversizedChunkedResponseDuringStreaming() throws Exception {
        var server = HttpServer.create(new InetSocketAddress(0), 0);
        server.setExecutor(command -> Thread.startVirtualThread(command));
        server.createContext("/internal/v1/customer-service/knowledge:delete", exchange -> {
            try {
                exchange.getRequestBody().readAllBytes();
                exchange.sendResponseHeaders(500, 0);
                for (int i = 0; i < 100; i++) {
                    exchange.getResponseBody().write("secret!!".getBytes(StandardCharsets.UTF_8));
                    exchange.getResponseBody().flush();
                }
            } catch (Exception ignored) {
            } finally {
                exchange.close();
            }
        });
        server.start();
        try {
            var props = new CustomerServiceProperties();
            props.setServiceUrl("http://localhost:" + server.getAddress().getPort());
            props.setTimeoutSeconds(2);
            props.setMaxResponseBytes(32);
            var ai = new AiEngineProperties(); ai.setToken("token");
            var error = assertTimeoutPreemptively(Duration.ofSeconds(2),
                    () -> assertThrows(CustomerServiceAiClient.CallException.class,
                            () -> new CustomerServiceAiClient(props, ai).delete(deleteRequest())));
            assertEquals("KNOWLEDGE_AI_RESPONSE_TOO_LARGE", error.code());
            assertFalse(error.getMessage().contains("secret"));
        } finally { server.stop(0); }
    }

    @Test
    void rebuildRequestLimitUsesUtf8BytesBeforeNetworkSend() throws Exception {
        var requests = new AtomicInteger();
        var server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/internal/v1/customer-service/knowledge:rebuild", exchange -> {
            requests.incrementAndGet();
            writeJson(exchange, "{}");
        });
        server.start();
        try {
            var props = new CustomerServiceProperties();
            props.setServiceUrl("http://localhost:" + server.getAddress().getPort());
            var ai = new AiEngineProperties(); ai.setToken("token");
            var lease = new KnowledgeMutationCoordinator.Lease(
                    "collection:customer_service_knowledge", "op_2", "REBUILD", 4, 2000000000L, "proof");
            var request = new CustomerServiceAiClient.RebuildRequest("customer_service_knowledge", List.of(
                    new CustomerServiceAiClient.RebuildDocument("1", 1, "中文文件名".repeat(20), "TXT",
                            "https://example.com/a", "a".repeat(64))), "9", lease);
            byte[] utf8 = new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsBytes(request);
            String json = new com.fasterxml.jackson.databind.ObjectMapper().writeValueAsString(request);
            assertTrue(utf8.length > json.length());
            props.setRebuildMaxRequestBytes(json.length());

            var error = assertThrows(CustomerServiceAiClient.CallException.class,
                    () -> new CustomerServiceAiClient(props, ai).rebuild(request));
            assertEquals("KNOWLEDGE_REBUILD_REQUEST_TOO_LARGE", error.code());
            assertEquals(0, requests.get());
        } finally { server.stop(0); }
    }

    @Test
    void indexSuccessRequiresExactOperationStatusAndIdentity() throws Exception {
        var responseBody = new AtomicReference<>("{}");
        var server = jsonServer("/internal/v1/customer-service/knowledge:etl", responseBody);
        try {
            var client = client(server);
            var lease = new KnowledgeMutationCoordinator.Lease("document:1", "op_1", "INDEX", 3, 2000000000L, "proof");
            var request = new CustomerServiceAiClient.IndexRequest("1", 2, "a.txt", "TXT",
                    "https://example.com/file", "a".repeat(64), "7", lease);
            for (String invalid : List.of(
                    "{}",
                    "{\"error\":{\"code\":\"signed-secret\"}}",
                    "{\"operation\":\"DELETE\",\"status\":\"SUCCEEDED\",\"documentId\":\"1\",\"documentVersion\":2,\"chunkCount\":1,\"idempotent\":false}",
                    "{\"operation\":\"INDEX\",\"status\":\"FAILED\",\"documentId\":\"1\",\"documentVersion\":2,\"chunkCount\":1,\"idempotent\":false}",
                    "{\"operation\":\"INDEX\",\"status\":\"SUCCEEDED\",\"documentId\":\"other\",\"documentVersion\":2,\"chunkCount\":1,\"idempotent\":false}",
                    "{\"operation\":\"INDEX\",\"status\":\"SUCCEEDED\",\"documentId\":\"1\",\"documentVersion\":3,\"chunkCount\":1,\"idempotent\":false}",
                    "{\"operation\":\"INDEX\",\"status\":\"SUCCEEDED\",\"documentId\":\"1\",\"documentVersion\":2,\"chunkCount\":1000001,\"idempotent\":false}")) {
                responseBody.set(invalid);
                var error = assertThrows(CustomerServiceAiClient.CallException.class, () -> client.index(request));
                assertEquals("KNOWLEDGE_AI_RESPONSE_INVALID", error.code());
                assertFalse(error.getMessage().contains("signed-secret"));
            }
            responseBody.set("{\"operation\":\"INDEX\",\"status\":\"SUCCEEDED\",\"documentId\":\"1\",\"documentVersion\":2,\"chunkCount\":4,\"idempotent\":false}");
            assertEquals(4, client.index(request).chunkCount());
        } finally { server.stop(0); }
    }

    @Test
    void deleteAndRebuildUseDedicatedSuccessSchemas() throws Exception {
        var responseBody = new AtomicReference<>("{\"operation\":\"DELETE\",\"status\":\"SUCCEEDED\",\"documentId\":\"1\",\"documentVersion\":2,\"chunkCount\":0,\"idempotent\":true}");
        var server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext("/internal/v1/customer-service/knowledge:delete", exchange -> writeJson(exchange, responseBody.get()));
        server.createContext("/internal/v1/customer-service/knowledge:rebuild", exchange -> writeJson(exchange, responseBody.get()));
        server.start();
        try {
            var client = client(server);
            var deleteLease = new KnowledgeMutationCoordinator.Lease("document:1", "op_1", "DELETE", 3, 2000000000L, "proof");
            assertTrue(client.delete(new CustomerServiceAiClient.DeleteRequest("1", 2, deleteLease)).idempotent());
            responseBody.set("{\"operation\":\"REBUILD\",\"status\":\"SUCCEEDED\",\"collectionAlias\":\"customer_service_knowledge\",\"etlVersion\":\"9\",\"documentCount\":2,\"idempotent\":false}");
            var rebuildLease = new KnowledgeMutationCoordinator.Lease("collection:customer_service_knowledge", "op_2", "REBUILD", 4, 2000000000L, "proof");
            var rebuildDocuments = List.of(
                    new CustomerServiceAiClient.RebuildDocument("1", 1, "a.txt", "TXT", "https://example.com/a", "a".repeat(64)),
                    new CustomerServiceAiClient.RebuildDocument("2", 1, "b.txt", "TXT", "https://example.com/b", "b".repeat(64)));
            var result = client.rebuild(new CustomerServiceAiClient.RebuildRequest(
                    "customer_service_knowledge", rebuildDocuments, "9", rebuildLease));
            assertEquals(2, result.documentCount());
            responseBody.set("{\"operation\":\"REBUILD\",\"status\":\"SUCCEEDED\",\"collectionAlias\":\"customer_service_knowledge\",\"etlVersion\":\"9\",\"documentCount\":0,\"idempotent\":true}");
            var emptyResult = client.rebuild(new CustomerServiceAiClient.RebuildRequest(
                    "customer_service_knowledge", List.of(), "9", rebuildLease));
            assertEquals(0, emptyResult.documentCount());
            assertTrue(emptyResult.idempotent());
            responseBody.set("{\"operation\":\"REBUILD\",\"status\":\"SUCCEEDED\",\"collectionAlias\":\"other\",\"etlVersion\":\"9\",\"documentCount\":2,\"idempotent\":false}");
            assertThrows(CustomerServiceAiClient.CallException.class, () -> client.rebuild(
                    new CustomerServiceAiClient.RebuildRequest("customer_service_knowledge", rebuildDocuments, "9", rebuildLease)));
        } finally { server.stop(0); }
    }

    private static HttpServer jsonServer(String path, AtomicReference<String> responseBody) throws Exception {
        var server = HttpServer.create(new InetSocketAddress(0), 0);
        server.createContext(path, exchange -> writeJson(exchange, responseBody.get()));
        server.start();
        return server;
    }

    private static void writeJson(com.sun.net.httpserver.HttpExchange exchange, String value) throws java.io.IOException {
        exchange.getRequestBody().readAllBytes();
        byte[] response = value.getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().add("Content-Type", "application/json");
        exchange.sendResponseHeaders(200, response.length);
        exchange.getResponseBody().write(response); exchange.close();
    }

    private static CustomerServiceAiClient client(HttpServer server) {
        var props = new CustomerServiceProperties();
        props.setServiceUrl("http://localhost:" + server.getAddress().getPort());
        var ai = new AiEngineProperties(); ai.setToken("token");
        return new CustomerServiceAiClient(props, ai);
    }

    private static CustomerServiceAiClient timeoutClient(HttpServer server) {
        var props = new CustomerServiceProperties();
        props.setServiceUrl("http://localhost:" + server.getAddress().getPort());
        props.setTimeoutSeconds(1);
        var ai = new AiEngineProperties(); ai.setToken("token");
        return new CustomerServiceAiClient(props, ai);
    }

    private static CustomerServiceAiClient.DeleteRequest deleteRequest() {
        return new CustomerServiceAiClient.DeleteRequest("1", 1,
                new KnowledgeMutationCoordinator.Lease("document:1", "op_1", "DELETE", 3, 2000000000L, "proof"));
    }
}
