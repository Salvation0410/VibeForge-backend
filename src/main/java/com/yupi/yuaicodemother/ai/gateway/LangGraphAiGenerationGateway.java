package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.HttpStatusCode;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.time.Duration;
import java.util.Map;

/** 调用独立 LangGraph 服务并把 NDJSON 事件适配为历史 SSE 使用的流。 */
@Slf4j
@Component
@RequiredArgsConstructor
public class LangGraphAiGenerationGateway implements AiGenerationGateway {
    private final AiEngineProperties properties;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10)).build();

    @Override
    public CodeGenTypeEnum route(String prompt, Long appId, Long userId, String requestId) {
        try {
            Map<String, Object> body = Map.of("prompt", prompt,
                    "appId", appId == null ? "" : String.valueOf(appId),
                    "requestId", requestId == null ? "" : requestId,
                    "userId", userId == null ? "" : String.valueOf(userId));
            HttpResponse<String> response = httpClient.send(buildRequest("/internal/v1/route", body),
                    HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8));
            ensureSuccess(response.statusCode(), response.body());
            String value = objectMapper.readTree(response.body()).path("codeGenType").asText();
            CodeGenTypeEnum type = CodeGenTypeEnum.getEnumByValue(value.toLowerCase());
            if (type == null) {
                throw new IllegalStateException("LangGraph returned unsupported codeGenType: " + value);
            }
            return type;
        } catch (Exception e) {
            throw new IllegalStateException("LangGraph route request failed", e);
        }
    }

    @Override
    public Flux<String> generate(String prompt, CodeGenTypeEnum codeGenType, Long appId, Long userId, String requestId) {
        return Flux.create(sink -> {
            try {
                Map<String, Object> body = Map.of(
                        "requestId", requestId,
                        "appId", String.valueOf(appId),
                        "prompt", prompt,
                        "codeGenType", codeGenType.name(),
                        "metadata", Map.of("userId", userId == null ? "" : String.valueOf(userId)));
                HttpRequest request = buildRequest("/internal/v1/generations:stream", body);
                httpClient.sendAsync(request, HttpResponse.BodyHandlers.ofInputStream())
                        .whenComplete((response, error) -> {
                            if (error != null) { sink.error(error); return; }
                            if (response.statusCode() / 100 != 2) {
                                sink.error(new IllegalStateException("LangGraph generation failed: HTTP " + response.statusCode()));
                                return;
                            }
                            Thread.startVirtualThread(() -> {
                                try (BufferedReader reader = new BufferedReader(new InputStreamReader(response.body(), StandardCharsets.UTF_8))) {
                                    String line;
                                    while ((line = reader.readLine()) != null) {
                                        String message = eventToLegacyMessage(line, codeGenType);
                                        if (!message.isBlank()) sink.next(message);
                                    }
                                    sink.complete();
                                } catch (Exception streamError) {
                                    sink.error(streamError);
                                }
                            });
                        });
            } catch (Exception e) { sink.error(e); }
        });
    }

    private HttpRequest buildRequest(String path, Object body) throws Exception {
        String json = objectMapper.writeValueAsString(body);
        return HttpRequest.newBuilder(URI.create(properties.getServiceUrl().replaceAll("/$", "") + path))
                .timeout(Duration.ofMinutes(10)).header("Content-Type", "application/json")
                .header("Authorization", "Bearer " + properties.getToken())
                .POST(HttpRequest.BodyPublishers.ofString(json, StandardCharsets.UTF_8)).build();
    }

    private String eventToLegacyMessage(String line, CodeGenTypeEnum codeGenType) {
        try {
            if (line == null || line.isBlank()) return "";
            JsonNode event = objectMapper.readTree(line);
            String type = event.path("type").asText();
            JsonNode data = event.path("data");
            if ("content_delta".equals(type)) {
                if (codeGenType != CodeGenTypeEnum.VUE_PROJECT) {
                    return data.path("content").asText();
                }
                return objectMapper.writeValueAsString(Map.of("type", "ai_response", "data", data.path("content").asText()));
            }
            if ("tool_started".equals(type)) {
                return objectMapper.writeValueAsString(Map.of("type", "tool_request", "id", data.path("toolCallId").asText(), "name", data.path("tool").asText(), "arguments", "{}"));
            }
            if ("tool_finished".equals(type)) {
                return objectMapper.writeValueAsString(Map.of("type", "tool_executed", "id", data.path("toolCallId").asText(), "name", data.path("tool").asText(), "arguments", objectMapper.writeValueAsString(data.path("result"))));
            }
            if ("failed".equals(type)) {
                throw new IllegalStateException(event.path("error").path("message").asText("LangGraph generation failed"));
            }
            return "";
        } catch (Exception e) {
            throw new IllegalStateException("Invalid LangGraph NDJSON event", e);
        }
    }

    private void ensureSuccess(int status, String body) {
        if (HttpStatusCode.valueOf(status).is2xxSuccessful()) return;
        throw new IllegalStateException("LangGraph request failed: HTTP " + status + " " + body);
    }
}
