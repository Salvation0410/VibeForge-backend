package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;

import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;

import static org.junit.jupiter.api.Assertions.*;

class LangGraphAiGenerationGatewayTest {
    private HttpServer server;

    @AfterEach
    void stopServer() {
        if (server != null) server.stop(0);
    }

    @Test
    void multiFileEmitsOnlyLastCandidateAfterCompleted() throws Exception {
        String body = event("content_delta", "{\"content\":\"first\"}")
                + event("tool_started", "{\"tool\":\"artifact_validate\",\"toolCallId\":\"t1\"}")
                + event("content_delta", "{\"content\":\"repaired\"}")
                + event("tool_finished", "{\"tool\":\"artifact_publish\",\"toolCallId\":\"t2\",\"result\":{}}")
                + event("completed", "{}");
        var gateway = gatewayReturning(body);

        var chunks = gateway.generate("build", CodeGenTypeEnum.MULTI_FILE, 42L, 7L, "req-1")
                .collectList().block();

        assertEquals(java.util.List.of("repaired"), chunks);
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
