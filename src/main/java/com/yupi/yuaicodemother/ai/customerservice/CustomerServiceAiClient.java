package com.yupi.yuaicodemother.ai.customerservice;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;

@Component
public class CustomerServiceAiClient {
    private final CustomerServiceProperties properties;
    private final AiEngineProperties aiProperties;
    private final ObjectMapper objectMapper;
    private final HttpClient httpClient;

    public CustomerServiceAiClient(CustomerServiceProperties properties, AiEngineProperties aiProperties) {
        this(properties, aiProperties, new ObjectMapper(), HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_1_1).connectTimeout(Duration.ofSeconds(5)).build());
    }

    CustomerServiceAiClient(CustomerServiceProperties properties, AiEngineProperties aiProperties,
                            ObjectMapper objectMapper, HttpClient httpClient) {
        this.properties = properties;
        this.aiProperties = aiProperties;
        this.objectMapper = objectMapper;
        this.httpClient = httpClient;
    }

    public Result index(IndexRequest request) { return call("/internal/v1/customer-service/knowledge:etl", request); }
    public Result delete(DeleteRequest request) { return call("/internal/v1/customer-service/knowledge:delete", request); }
    public Result rebuild(RebuildRequest request) { return call("/internal/v1/customer-service/knowledge:rebuild", request); }

    public boolean health() {
        try {
            HttpRequest request = baseRequest("/internal/v1/customer-service/health").GET().build();
            HttpResponse<InputStream> response = httpClient.send(request, HttpResponse.BodyHandlers.ofInputStream());
            byte[] body = readBounded(response.body());
            return response.statusCode() / 100 == 2 && objectMapper.readTree(body).path("ready").asBoolean(false);
        } catch (Exception error) {
            return false;
        }
    }

    private Result call(String path, Object body) {
        try {
            String json = objectMapper.writeValueAsString(body);
            HttpRequest request = baseRequest(path).header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofString(json, StandardCharsets.UTF_8)).build();
            HttpResponse<InputStream> response = httpClient.send(request, HttpResponse.BodyHandlers.ofInputStream());
            byte[] bytes = readBounded(response.body());
            JsonNode payload = objectMapper.readTree(bytes);
            if (response.statusCode() / 100 != 2 || payload.has("error")) {
                String code = payload.path("error").path("code").asText("KNOWLEDGE_AI_HTTP_" + response.statusCode());
                boolean transientFailure = response.statusCode() >= 500 || response.statusCode() == 408 || response.statusCode() == 429;
                throw new CallException(code, transientFailure);
            }
            return new Result(payload.path("chunkCount").asInt(0), payload.path("idempotent").asBoolean(false));
        } catch (CallException error) {
            throw error;
        } catch (InterruptedException error) {
            Thread.currentThread().interrupt();
            throw new CallException("KNOWLEDGE_AI_INTERRUPTED", true);
        } catch (Exception error) {
            throw new CallException("KNOWLEDGE_AI_UNAVAILABLE", true);
        }
    }

    private HttpRequest.Builder baseRequest(String path) {
        String base = properties.getServiceUrl().replaceAll("/$", "");
        return HttpRequest.newBuilder(URI.create(base + path))
                .timeout(Duration.ofSeconds(Math.max(1, properties.getTimeoutSeconds())))
                .header("Authorization", "Bearer " + aiProperties.getToken());
    }

    private byte[] readBounded(InputStream input) throws Exception {
        try (input; ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            int total = 0;
            for (int count; (count = input.read(buffer)) >= 0;) {
                total += count;
                if (total > properties.getMaxResponseBytes()) throw new CallException("KNOWLEDGE_AI_RESPONSE_TOO_LARGE", true);
                output.write(buffer, 0, count);
            }
            return output.toByteArray();
        }
    }

    public record IndexRequest(String documentId, long documentVersion, String fileName, String fileType,
                               String signedUrl, String sha256, String etlVersion,
                               KnowledgeMutationCoordinator.Lease lease) {
        @JsonProperty("operation")
        public String operation() { return "INDEX"; }
    }
    public record DeleteRequest(String documentId, long documentVersion,
                                KnowledgeMutationCoordinator.Lease lease) {
        @JsonProperty("operation")
        public String operation() { return "DELETE"; }
    }
    public record RebuildDocument(String documentId, long documentVersion, String fileName, String fileType,
                                  String signedUrl, String sha256) { }
    public record RebuildRequest(List<RebuildDocument> documents, String etlVersion,
                                 KnowledgeMutationCoordinator.Lease lease) {
        @JsonProperty("operation")
        public String operation() { return "REBUILD"; }
    }
    public record Result(int chunkCount, boolean idempotent) { }

    public static final class CallException extends RuntimeException {
        private final String code;
        private final boolean transientFailure;
        public CallException(String code, boolean transientFailure) {
            super("Customer-service AI request failed: " + code);
            this.code = code;
            this.transientFailure = transientFailure;
        }
        public String code() { return code; }
        public boolean transientFailure() { return transientFailure; }
    }
}
