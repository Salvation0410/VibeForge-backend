package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import reactor.core.publisher.BaseSubscriber;
import reactor.core.publisher.Flux;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;

class LangGraphAiGenerationGatewayTest {
    private HttpServer server;
    private ExecutorService serverExecutor;

    @AfterEach
    void stopServer() {
        if (server != null) server.stop(0);
        if (serverExecutor != null) serverExecutor.shutdownNow();
    }

    @Test
    void multiFileEmitsOnlyLastCandidateAfterCompleted() throws Exception {
        String body = event("content_delta", "{\"content\":\"first\"}")
                + event("tool_started", "{\"tool\":\"artifact_validate\",\"toolCallId\":\"t1\"}")
                + event("content_delta", "{\"content\":\"repaired\"}")
                + event("tool_finished", "{\"tool\":\"artifact_publish\",\"toolCallId\":\"t2\",\"result\":{}}")
                + event("completed", "{\"threadId\":\"42:req-1\",\"codeGenType\":\"MULTI_FILE\","
                + "\"qualityPassed\":true,\"repairCount\":1,\"toolCallCount\":0,"
                + "\"published\":true,\"versionId\":\"req-1\",\"artifactHashes\":{}}");
        var gateway = gatewayReturning(body);

        var chunks = gateway.generate("build", CodeGenTypeEnum.MULTI_FILE, 42L, 7L, "req-1")
                .collectList().block();

        assertEquals(java.util.List.of("repaired"), chunks);
    }

    @Test
    void completedWithoutStaticCandidateDoesNotInventContent() throws Exception {
        var gateway = gatewayReturning(event("completed",
                "{\"threadId\":\"42:req-empty\",\"codeGenType\":\"HTML\",\"published\":true}"));

        var chunks = gateway.generate("build", CodeGenTypeEnum.HTML, 42L, 7L, "req-empty")
                .collectList().block();

        assertEquals(java.util.List.of(), chunks);
    }

    @Test
    void eofWithoutCompletedIsAnError() throws Exception {
        var gateway = gatewayReturning(event("content_delta", "{\"content\":\"partial\"}"));

        GenerationStreamException error = assertThrows(GenerationStreamException.class,
                () -> gateway.generate("build", CodeGenTypeEnum.MULTI_FILE, 42L, 7L, "req-eof")
                        .collectList().block());

        assertEquals("LANGGRAPH_STREAM_INCOMPLETE", error.getErrorCode());
    }

    @Test
    void failedEventPreservesStableErrorCode() throws Exception {
        String body = "{\"requestId\":\"req-fail\",\"type\":\"failed\","
                + "\"data\":{},\"error\":{\"code\":\"MODEL_OUTPUT_TRUNCATED\",\"message\":\"truncated\"}}\n";
        var gateway = gatewayReturning(body);

        GenerationStreamException error = assertThrows(GenerationStreamException.class,
                () -> gateway.generate("build", CodeGenTypeEnum.MULTI_FILE, 42L, 7L, "req-fail")
                        .collectList().block());

        assertEquals("MODEL_OUTPUT_TRUNCATED", error.getErrorCode());
        assertEquals("req-fail", error.getRequestId());
    }

    @Test
    void routeUsesHttp11WithoutH2cUpgrade() throws Exception {
        AtomicReference<String> protocol = new AtomicReference<>();
        AtomicReference<String> upgrade = new AtomicReference<>();
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/internal/v1/route", exchange -> {
            protocol.set(exchange.getProtocol());
            upgrade.set(exchange.getRequestHeaders().getFirst("Upgrade"));
            exchange.getRequestBody().readAllBytes();
            byte[] bytes = "{\"codeGenType\":\"HTML\"}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        AiEngineProperties properties = new AiEngineProperties();
        properties.setServiceUrl("http://127.0.0.1:" + server.getAddress().getPort());
        properties.setToken("test-token");
        var gateway = new LangGraphAiGenerationGateway(properties, new ObjectMapper());

        assertEquals(CodeGenTypeEnum.HTML, gateway.route("build", null, 7L, "req-route"));
        assertEquals("HTTP/1.1", protocol.get());
        assertNull(upgrade.get(), "LangGraph requests must not attempt an h2c upgrade");
    }

    @Test
    void cancellingSubscriberClosesNdjsonResponseBody() throws Exception {
        CountDownLatch firstChunkReceived = new CountDownLatch(1);
        CountDownLatch clientClosed = new CountDownLatch(1);
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/internal/v1/generations:stream", exchange -> {
            exchange.getRequestBody().readAllBytes();
            exchange.sendResponseHeaders(200, 0);
            try {
                int sequence = 0;
                while (true) {
                    String chunk = "{\"requestId\":\"req-cancel\",\"type\":\"content_delta\","
                            + "\"sequence\":" + sequence++ + ",\"data\":{\"content\":\"chunk\"}}\n";
                    exchange.getResponseBody().write(chunk.getBytes(StandardCharsets.UTF_8));
                    exchange.getResponseBody().flush();
                    Thread.sleep(10);
                }
            } catch (IOException expected) {
                clientClosed.countDown();
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } finally {
                exchange.close();
            }
        });
        server.start();
        AiEngineProperties properties = new AiEngineProperties();
        properties.setServiceUrl("http://127.0.0.1:" + server.getAddress().getPort());
        properties.setToken("test-token");
        var gateway = new LangGraphAiGenerationGateway(properties, new ObjectMapper());

        gateway.generate("build", CodeGenTypeEnum.VUE_PROJECT, 42L, 7L, "req-cancel")
                .subscribe(new BaseSubscriber<>() {
                    @Override
                    protected void hookOnSubscribe(org.reactivestreams.Subscription subscription) {
                        request(1);
                    }

                    @Override
                    protected void hookOnNext(String value) {
                        firstChunkReceived.countDown();
                        cancel();
                    }
                });

        assertTrue(firstChunkReceived.await(5, TimeUnit.SECONDS), "subscriber did not receive the first chunk");
        assertTrue(clientClosed.await(5, TimeUnit.SECONDS),
                "cancelling the downstream subscriber must close the NDJSON response body");
    }

    @Test
    void idleNdjsonStreamFailsWithStableErrorAndClosesConnection() throws Exception {
        CountDownLatch clientClosed = new CountDownLatch(1);
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/internal/v1/generations:stream", exchange -> {
            exchange.getRequestBody().readAllBytes();
            exchange.sendResponseHeaders(200, 0);
            String chunk = "{\"requestId\":\"req-idle\",\"type\":\"node_status\","
                    + "\"data\":{\"node\":\"generate_html\",\"status\":\"started\"}}\n";
            exchange.getResponseBody().write(chunk.getBytes(StandardCharsets.UTF_8));
            exchange.getResponseBody().flush();
            try {
                while (true) {
                    Thread.sleep(50);
                    exchange.getResponseBody().write(' ');
                    exchange.getResponseBody().flush();
                }
            } catch (IOException expected) {
                clientClosed.countDown();
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            } finally {
                exchange.close();
            }
        });
        server.start();
        AiEngineProperties properties = new AiEngineProperties();
        properties.setServiceUrl("http://127.0.0.1:" + server.getAddress().getPort());
        properties.setToken("test-token");
        properties.setGenerationStreamIdleTimeoutSeconds(1);
        var gateway = new LangGraphAiGenerationGateway(properties, new ObjectMapper());

        GenerationStreamException error = assertThrows(GenerationStreamException.class,
                () -> gateway.generate("build", CodeGenTypeEnum.HTML, 42L, 7L, "req-idle")
                        .collectList().block(Duration.ofSeconds(5)));

        assertEquals("LANGGRAPH_STREAM_IDLE_TIMEOUT", error.getErrorCode());
        assertEquals("req-idle", error.getRequestId());
        assertTrue(clientClosed.await(5, TimeUnit.SECONDS),
                "idle timeout must close the NDJSON response body");
    }

    @Test
    void concurrentLargeStaticStreamsKeepIndependentFinalCandidates() throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        serverExecutor = Executors.newFixedThreadPool(8);
        server.setExecutor(serverExecutor);
        server.createContext("/internal/v1/generations:stream", exchange -> {
            String requestBody = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            String requestId = new ObjectMapper().readTree(requestBody).path("requestId").asText();
            StringBuilder body = new StringBuilder();
            for (int index = 0; index < 500; index++) {
                body.append("{\"requestId\":\"").append(requestId)
                        .append("\",\"type\":\"content_delta\",\"data\":{\"content\":\"")
                        .append(requestId).append("-candidate-").append(index).append("\"}}\n");
            }
            body.append("{\"requestId\":\"").append(requestId)
                    .append("\",\"type\":\"completed\",\"data\":{}}\n");
            byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        AiEngineProperties properties = new AiEngineProperties();
        properties.setServiceUrl("http://127.0.0.1:" + server.getAddress().getPort());
        properties.setToken("test-token");
        var gateway = new LangGraphAiGenerationGateway(properties, new ObjectMapper());

        var results = Flux.range(0, 8)
                .flatMap(index -> {
                    String requestId = "large-" + index;
                    return gateway.generate("build", CodeGenTypeEnum.HTML, 42L, 7L, requestId)
                            .collectList()
                            .map(chunks -> Map.entry(requestId, chunks));
                }, 8)
                .collectList()
                .block(Duration.ofSeconds(20));

        assertNotNull(results);
        assertEquals(8, results.size());
        results.forEach(result -> assertEquals(
                java.util.List.of(result.getKey() + "-candidate-499"), result.getValue()));
    }

    /** 启动一次性本地 NDJSON 服务，避免测试依赖真实 Python 进程。 */
    private LangGraphAiGenerationGateway gatewayReturning(String body) throws Exception {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/internal/v1/generations:stream", exchange -> {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
        AiEngineProperties properties = new AiEngineProperties();
        properties.setServiceUrl("http://127.0.0.1:" + server.getAddress().getPort());
        properties.setToken("test-token");
        return new LangGraphAiGenerationGateway(properties, new ObjectMapper());
    }

    /** 构造最小合法 NDJSON 事件。 */
    private String event(String type, String data) {
        return "{\"requestId\":\"req-1\",\"type\":\"" + type + "\",\"data\":" + data + "}\n";
    }
}
