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
}
