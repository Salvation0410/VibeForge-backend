package com.yupi.yuaicodemother.ai.gateway;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.ErrorCode;
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
                                    String latestArtifact = null;
                                    boolean completed = false;
                                    while ((line = reader.readLine()) != null) {
                                        ParsedEvent event = parseEvent(line, codeGenType, requestId);
                                        if (event.artifact() != null) latestArtifact = event.artifact();
                                        if (!event.message().isBlank()) sink.next(event.message());
                                        if (event.completed()) {
                                            completed = true;
                                            if (codeGenType != CodeGenTypeEnum.VUE_PROJECT && latestArtifact != null) {
                                                sink.next(latestArtifact);
                                            }
                                            break;
                                        }
                                    }
                                    if (!completed) {
                                        throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(),
                                                "LANGGRAPH_STREAM_INCOMPLETE", requestId,
                                                "LangGraph 事件流未收到 completed 终态，已保留上一版本", null);
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
     * 向 Python 发送协作式取消；读取协程继续等待明确失败终态，调用方因此不会提前释放应用租约。
     */
    @Override
    public void cancel(Long appId, Long userId, String requestId) {
        try {
            HttpRequest request = buildRequest("/internal/v1/generations/" + requestId + ":cancel",
                    Map.of("appId", String.valueOf(appId)));
            httpClient.sendAsync(request, HttpResponse.BodyHandlers.discarding())
                    .whenComplete((response, error) -> {
                        if (error != null || response.statusCode() / 100 != 2) {
                            log.warn("LangGraph 取消请求失败, requestId={}, status={}", requestId,
                                    response == null ? null : response.statusCode(), error);
                        }
                    });
        } catch (Exception error) {
            log.warn("构造 LangGraph 取消请求失败, requestId={}", requestId, error);
        }
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
     * HTML 和多文件内容只保留最后一个完整候选，收到 completed 后才交给下游；
     * Vue 内容与工具事件转换为带类型的 JSON 消息，失败事件转换为带稳定错误码的异常。
     *
     * @param line Python 服务返回的一行 NDJSON 文本
     * @param codeGenType 当前应用代码生成类型
     * @param fallbackRequestId 事件缺少请求 ID 时使用的当前请求 ID
     * @return 包含前端消息、最终候选或成功终态标记的解析结果
     * @throws IllegalStateException 事件不是合法 JSON 时抛出
     */
    private ParsedEvent parseEvent(String line, CodeGenTypeEnum codeGenType, String fallbackRequestId) {
        try {
            if (line == null || line.isBlank()) return ParsedEvent.EMPTY;
            JsonNode event = objectMapper.readTree(line);
            String type = event.path("type").asText();
            JsonNode data = event.path("data");
            if ("content_delta".equals(type)) {
                if (codeGenType != CodeGenTypeEnum.VUE_PROJECT) {
                    return new ParsedEvent("", data.path("content").asText(), false);
                }
                return new ParsedEvent(objectMapper.writeValueAsString(
                        Map.of("type", "ai_response", "data", data.path("content").asText())), null, false);
            }
            if ("tool_started".equals(type) && codeGenType == CodeGenTypeEnum.VUE_PROJECT) {
                return new ParsedEvent(objectMapper.writeValueAsString(Map.of("type", "tool_request", "id", data.path("toolCallId").asText(), "name", data.path("tool").asText(), "arguments", "{}")), null, false);
            }
            if ("tool_finished".equals(type) && codeGenType == CodeGenTypeEnum.VUE_PROJECT) {
                return new ParsedEvent(objectMapper.writeValueAsString(Map.of("type", "tool_executed", "id", data.path("toolCallId").asText(), "name", data.path("tool").asText(), "arguments", objectMapper.writeValueAsString(data.path("result")))), null, false);
            }
            if ("failed".equals(type)) {
                JsonNode error = event.path("error");
                String requestId = event.path("requestId").asText(fallbackRequestId);
                throw new GenerationStreamException(ErrorCode.OPERATION_ERROR.getCode(),
                        error.path("code").asText("GENERATION_FAILED").toUpperCase(), requestId,
                        error.path("message").asText("LangGraph generation failed"), null);
            }
            if ("completed".equals(type)) return new ParsedEvent("", null, true);
            return ParsedEvent.EMPTY;
        } catch (GenerationStreamException e) {
            throw e;
        } catch (Exception e) {
            throw new IllegalStateException("Invalid LangGraph NDJSON event", e);
        }
    }

    /** 表示一条内部事件转换后的前端消息、最终候选或成功终态。 */
    private record ParsedEvent(String message, String artifact, boolean completed) {
        private static final ParsedEvent EMPTY = new ParsedEvent("", null, false);
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
