package com.yupi.yuaicodemother.customerservice;

import com.sun.net.httpserver.HttpServer;
import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
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
}
