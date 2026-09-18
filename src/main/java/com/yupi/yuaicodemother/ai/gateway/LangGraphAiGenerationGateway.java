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

/**
 * 独立 LangGraph AI 服务的 HTTP 适配器，将内部 NDJSON 事件转换为既有 SSE 处理链可消费的数据。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class LangGraphAiGenerationGateway implements AiGenerationGateway {
    private final AiEngineProperties properties;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(10)).build();

    /**
     * 同步调用 Python 路由接口，并将返回值转换为 Java 代码生成枚举。
     *
     * @param prompt 用户输入的应用生成需求
     * @param appId 应用 ID；创建阶段可以为空
     * @param userId 当前用户 ID
     * @param requestId 本次路由请求的唯一标识
     * @return LangGraph 服务选择的代码生成类型
     * @throws IllegalStateException 请求失败、响应非成功状态或返回未知类型时抛出
     */
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

    /**
     * 异步调用 Python 流式生成接口，并逐行消费 NDJSON 响应。
     * <p>
     * HTTP 或协议解析错误通过返回的 {@link Flux} 错误信号向下游传播。
     *
     * @param prompt 用户输入的代码生成或修改需求
     * @param codeGenType 应用代码生成类型
     * @param appId 应用 ID
     * @param userId 当前用户 ID，会写入请求元数据
     * @param requestId 本次生成请求的唯一标识
     * @return 已转换为旧流消息格式的字符串数据流
     */
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

    /**
     * 构造携带 JSON 请求体、超时设置和内部 Bearer 令牌的 HTTP 请求。
     *
     * @param path Python AI 服务内部接口路径
     * @param body 需要序列化为 JSON 的请求对象
     * @return 可由共享 {@link HttpClient} 发送的 HTTP 请求
     * @throws Exception 请求体序列化或 URI 构造失败时抛出
     */
    private HttpRequest buildRequest(String path, Object body) throws Exception {
        String json = objectMapper.writeValueAsString(body);
        return HttpRequest.newBuilder(URI.create(properties.getServiceUrl().replaceAll("/$", "") + path))
                .timeout(Duration.ofMinutes(10)).header("Content-Type", "application/json")
                .header("Authorization", "Bearer " + properties.getToken())
                .POST(HttpRequest.BodyPublishers.ofString(json, StandardCharsets.UTF_8)).build();
    }

    /**
     * 将单行 LangGraph NDJSON 事件转换为现有流处理器使用的消息格式。
     * <p>
     * HTML 和多文件内容事件直接返回文本；Vue 内容与工具事件转换为带类型的 JSON 消息；
     * 状态类事件不向前端输出，失败事件转换为异常。
     *
     * @param line Python 服务返回的一行 NDJSON 文本
     * @param codeGenType 当前应用代码生成类型
     * @return 旧流处理器可消费的文本；无需下发的事件返回空字符串
     * @throws IllegalStateException 事件不是合法 JSON 或事件声明失败时抛出
     */
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

    /**
     * 校验内部 HTTP 响应状态，非 2xx 状态统一转换为调用异常。
     *
     * @param status HTTP 状态码
     * @param body 错误响应正文，用于诊断调用失败原因
     * @throws IllegalStateException 响应状态不是 2xx 时抛出
     */
    private void ensureSuccess(int status, String body) {
        if (HttpStatusCode.valueOf(status).is2xxSuccessful()) return;
        throw new IllegalStateException("LangGraph request failed: HTTP " + status + " " + body);
    }
}
