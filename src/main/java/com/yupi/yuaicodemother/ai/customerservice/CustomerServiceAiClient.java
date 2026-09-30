package com.yupi.yuaicodemother.ai.customerservice;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import org.springframework.stereotype.Component;

import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.http.HttpTimeoutException;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.ByteBuffer;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;

@Component
public class CustomerServiceAiClient {
    private static final int MAX_CHUNK_COUNT = 1_000_000;
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

    public Result index(IndexRequest request) {
        JsonNode payload = call("/internal/v1/customer-service/knowledge:etl", request);
        validateDocumentResponse(payload, "INDEX", request.documentId(), request.documentVersion());
        int chunkCount = requiredInt(payload, "chunkCount", 0, MAX_CHUNK_COUNT);
        return new Result(chunkCount, requiredBoolean(payload, "idempotent"));
    }

    public Result delete(DeleteRequest request) {
        JsonNode payload = call("/internal/v1/customer-service/knowledge:delete", request);
        validateDocumentResponse(payload, "DELETE", request.documentId(), request.documentVersion());
        if (requiredInt(payload, "chunkCount", 0, MAX_CHUNK_COUNT) != 0) invalidResponse();
        return new Result(0, requiredBoolean(payload, "idempotent"));
    }

    public RebuildResult rebuild(RebuildRequest request) {
        if (request.documents().size() > properties.getRebuildMaxDocuments())
            throw new CallException("KNOWLEDGE_REBUILD_TOO_MANY_DOCUMENTS", false);
        JsonNode payload = call("/internal/v1/customer-service/knowledge:rebuild", request);
        requireText(payload, "operation", "REBUILD");
        requireText(payload, "status", "SUCCEEDED");
        requireText(payload, "collectionAlias", request.collectionAlias());
        requireText(payload, "etlVersion", request.etlVersion());
        int documentCount = requiredInt(payload, "documentCount", 0, 1_000_000);
        if (documentCount != request.documents().size()) invalidResponse();
        return new RebuildResult(documentCount, requiredBoolean(payload, "idempotent"));
    }

    public boolean health() {
        try {
            HttpRequest request = baseRequest("/internal/v1/customer-service/health").GET().build();
            HttpResponse<byte[]> response = sendBounded(request);
            return response.statusCode() / 100 == 2 && objectMapper.readTree(response.body()).path("ready").asBoolean(false);
        } catch (Exception error) {
            return false;
        }
    }

    private JsonNode call(String path, Object body) {
        try {
            byte[] json = serializeRequest(body);
            HttpRequest request = baseRequest(path).header("Content-Type", "application/json")
                    .POST(HttpRequest.BodyPublishers.ofByteArray(json)).build();
            HttpResponse<byte[]> response = sendBounded(request);
            byte[] bytes = response.body();
            if (response.statusCode() / 100 != 2) {
                String code = errorCode(bytes, "KNOWLEDGE_AI_HTTP_" + response.statusCode());
                boolean transientFailure = response.statusCode() >= 500 || response.statusCode() == 408 || response.statusCode() == 429;
                throw new CallException(code, transientFailure);
            }
            JsonNode payload = objectMapper.readTree(bytes);
            if (payload == null || !payload.isObject() || payload.has("error")) {
                throw new CallException(payload == null ? "KNOWLEDGE_AI_RESPONSE_INVALID"
                        : stableErrorCode(payload, "KNOWLEDGE_AI_RESPONSE_INVALID"), false);
            }
            return payload;
        } catch (CallException error) {
            throw error;
        } catch (com.fasterxml.jackson.core.JsonProcessingException error) {
            throw new CallException("KNOWLEDGE_AI_RESPONSE_INVALID", false);
        } catch (Exception error) {
            throw new CallException("KNOWLEDGE_AI_UNAVAILABLE", true);
        }
    }

    private byte[] serializeRequest(Object body) {
        try {
            byte[] bytes = objectMapper.writeValueAsBytes(body);
            if (body instanceof RebuildRequest && bytes.length > properties.getRebuildMaxRequestBytes())
                throw new CallException("KNOWLEDGE_REBUILD_REQUEST_TOO_LARGE", false);
            return bytes;
        } catch (CallException error) {
            throw error;
        } catch (com.fasterxml.jackson.core.JsonProcessingException error) {
            throw new CallException("KNOWLEDGE_AI_REQUEST_INVALID", false);
        }
    }

    private HttpResponse<byte[]> sendBounded(HttpRequest request) {
        BoundedBodyHandler handler = new BoundedBodyHandler(properties.getMaxResponseBytes());
        CompletableFuture<HttpResponse<byte[]>> future = httpClient.sendAsync(request, handler);
        try {
            return future.get(Math.max(1, properties.getTimeoutSeconds()), TimeUnit.SECONDS);
        } catch (TimeoutException error) {
            handler.cancel();
            future.cancel(true);
            throw new CallException("KNOWLEDGE_AI_TIMEOUT", true);
        } catch (InterruptedException error) {
            handler.cancel();
            future.cancel(true);
            Thread.currentThread().interrupt();
            throw new CallException("KNOWLEDGE_AI_INTERRUPTED", true);
        } catch (ExecutionException error) {
            Throwable cause = unwrap(error.getCause());
            if (cause instanceof ResponseTooLargeException)
                throw new CallException("KNOWLEDGE_AI_RESPONSE_TOO_LARGE", true);
            if (cause instanceof HttpTimeoutException)
                throw new CallException("KNOWLEDGE_AI_TIMEOUT", true);
            throw new CallException("KNOWLEDGE_AI_UNAVAILABLE", true);
        }
    }

    private static Throwable unwrap(Throwable error) {
        Throwable current = error;
        while ((current instanceof java.util.concurrent.CompletionException
                || current instanceof ExecutionException) && current.getCause() != null) {
            current = current.getCause();
        }
        return current;
    }

    private String errorCode(byte[] bytes, String fallback) {
        try {
            JsonNode payload = objectMapper.readTree(bytes);
            return stableErrorCode(payload, fallback);
        } catch (Exception ignored) {
            return fallback;
        }
    }

    private static String stableErrorCode(JsonNode payload, String fallback) {
        String code = payload == null ? "" : payload.path("error").path("code").asText("");
        return code.matches("[A-Z][A-Z0-9_]{0,127}") ? code : fallback;
    }

    private static void validateDocumentResponse(JsonNode payload, String operation,
                                                 String documentId, long documentVersion) {
        requireText(payload, "operation", operation);
        requireText(payload, "status", "SUCCEEDED");
        requireText(payload, "documentId", documentId);
        JsonNode version = payload.get("documentVersion");
        if (version == null || !version.isIntegralNumber() || version.longValue() != documentVersion) invalidResponse();
    }

    private static void requireText(JsonNode payload, String field, String expected) {
        JsonNode value = payload.get(field);
        if (value == null || !value.isTextual() || !expected.equals(value.textValue())) invalidResponse();
    }

    private static int requiredInt(JsonNode payload, String field, int minimum, int maximum) {
        JsonNode value = payload.get(field);
        if (value == null || !value.isIntegralNumber() || !value.canConvertToInt()
                || value.intValue() < minimum || value.intValue() > maximum) invalidResponse();
        return value.intValue();
    }

    private static boolean requiredBoolean(JsonNode payload, String field) {
        JsonNode value = payload.get(field);
        if (value == null || !value.isBoolean()) invalidResponse();
        return value.booleanValue();
    }

    private static void invalidResponse() {
        throw new CallException("KNOWLEDGE_AI_RESPONSE_INVALID", false);
    }

    private HttpRequest.Builder baseRequest(String path) {
        String base = properties.getServiceUrl().replaceAll("/$", "");
        return HttpRequest.newBuilder(URI.create(base + path))
                .timeout(Duration.ofSeconds(Math.max(1, properties.getTimeoutSeconds())))
                .header("Authorization", "Bearer " + aiProperties.getToken());
    }

    private static final class BoundedBodyHandler implements HttpResponse.BodyHandler<byte[]> {
        private final int maximumBytes;
        private final AtomicReference<BoundedBodySubscriber> subscriber = new AtomicReference<>();

        private BoundedBodyHandler(int maximumBytes) { this.maximumBytes = maximumBytes; }

        @Override
        public HttpResponse.BodySubscriber<byte[]> apply(HttpResponse.ResponseInfo responseInfo) {
            BoundedBodySubscriber created = new BoundedBodySubscriber(maximumBytes);
            subscriber.set(created);
            responseInfo.headers().firstValueAsLong("Content-Length")
                    .ifPresent(length -> { if (length > maximumBytes) created.reject(); });
            return created;
        }

        private void cancel() {
            BoundedBodySubscriber current = subscriber.get();
            if (current != null) current.cancel();
        }
    }

    private static final class BoundedBodySubscriber implements HttpResponse.BodySubscriber<byte[]> {
        private final int maximumBytes;
        private final ByteArrayOutputStream output;
        private final CompletableFuture<byte[]> body = new CompletableFuture<>();
        private final AtomicBoolean done = new AtomicBoolean();
        private volatile Flow.Subscription subscription;
        private volatile boolean rejected;

        private BoundedBodySubscriber(int maximumBytes) {
            this.maximumBytes = maximumBytes;
            this.output = new ByteArrayOutputStream(Math.min(maximumBytes, 8192));
        }

        @Override
        public CompletableFuture<byte[]> getBody() { return body; }

        @Override
        public void onSubscribe(Flow.Subscription subscription) {
            this.subscription = subscription;
            if (rejected || done.get()) {
                subscription.cancel();
                failTooLarge();
            } else {
                subscription.request(1);
            }
        }

        @Override
        public void onNext(List<ByteBuffer> items) {
            if (done.get()) return;
            long incoming = items.stream().mapToLong(ByteBuffer::remaining).sum();
            if (incoming > maximumBytes - output.size()) {
                Flow.Subscription current = subscription;
                if (current != null) current.cancel();
                failTooLarge();
                return;
            }
            for (ByteBuffer item : items) {
                byte[] chunk = new byte[item.remaining()];
                item.get(chunk);
                output.writeBytes(chunk);
            }
            Flow.Subscription current = subscription;
            if (current != null) current.request(1);
        }

        @Override
        public void onError(Throwable throwable) {
            if (done.compareAndSet(false, true)) body.completeExceptionally(throwable);
        }

        @Override
        public void onComplete() {
            if (done.compareAndSet(false, true)) body.complete(output.toByteArray());
        }

        private void reject() {
            rejected = true;
            Flow.Subscription current = subscription;
            if (current != null) {
                current.cancel();
                failTooLarge();
            }
        }

        private void cancel() {
            Flow.Subscription current = subscription;
            if (current != null) current.cancel();
            if (done.compareAndSet(false, true)) body.cancel(true);
        }

        private void failTooLarge() {
            if (done.compareAndSet(false, true)) body.completeExceptionally(new ResponseTooLargeException());
        }
    }

    private static final class ResponseTooLargeException extends RuntimeException { }

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
    public record RebuildRequest(String collectionAlias, List<RebuildDocument> documents, String etlVersion,
                                 KnowledgeMutationCoordinator.Lease lease) {
        @JsonProperty("operation")
        public String operation() { return "REBUILD"; }
    }
    public record Result(int chunkCount, boolean idempotent) { }
    public record RebuildResult(int documentCount, boolean idempotent) { }

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
