package com.yupi.yuaicodemother.customerservice;

import com.aliyun.oss.HttpMethod;
import com.aliyun.oss.OSS;
import com.aliyun.oss.model.PutObjectRequest;
import com.yupi.yuaicodemother.config.OssProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.manager.OssManager;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.time.Instant;
import java.util.Date;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class OssKnowledgeDocumentTest {
    private OSS client;
    private OssManager manager;
    private OssProperties properties;

    @BeforeEach
    void setUp() {
        client = mock(OSS.class);
        properties = new OssProperties();
        properties.setBucketName("private-bucket");
        properties.setEndpoint("oss.example.test");
        properties.setAccessKeyId("secret-key-id");
        properties.setAccessKeySecret("secret-key-value");
        manager = new OssManager(properties, () -> client);
    }

    @Test
    void uploadReturnsOnlyPrivateMetadataAndClosesClient() throws Exception {
        byte[] bytes = "# 产品\n说明".getBytes(StandardCharsets.UTF_8);
        var file = new MockMultipartFile("file", "../guide.md", "text/markdown", bytes);
        var result = manager.uploadKnowledgeDocument(file);

        assertEquals("guide.md", result.displayName());
        assertEquals("md", result.fileType());
        assertEquals(bytes.length, result.size());
        assertTrue(result.objectKey().matches("customer-service-knowledge/[0-9a-f]{32}\\.md"));
        assertFalse(result.toString().contains("secret-key"));
        assertFalse(result.toString().contains("http"));
        var request = org.mockito.ArgumentCaptor.forClass(PutObjectRequest.class);
        verify(client).putObject(request.capture());
        assertEquals("private-bucket", request.getValue().getBucketName());
        assertEquals(result.objectKey(), request.getValue().getKey());
        assertEquals(bytes.length, request.getValue().getMetadata().getContentLength());
        assertEquals("text/markdown", request.getValue().getMetadata().getContentType());
        assertEquals("private", request.getValue().getMetadata().getRawMetadata().get("x-oss-object-acl"));
        assertArrayEquals(bytes, request.getValue().getInputStream().readAllBytes());
        verify(client).shutdown();
    }

    @Test
    void generatesShortLivedGetUrlAndDeletesOnlyKnowledgeKeys() throws Exception {
        String key = "customer-service-knowledge/0123456789abcdef0123456789abcdef.pdf";
        properties.setKnowledgeSignedUrlTtlSeconds(300);
        URL signed = new URL("https://private-bucket.example.test/object?signature=secret");
        when(client.generatePresignedUrl(eq("private-bucket"), eq(key), any(Date.class), eq(HttpMethod.GET)))
                .thenReturn(signed);
        Instant before = Instant.now();
        assertEquals(signed, manager.generateKnowledgeDownloadUrl(key));
        Instant after = Instant.now();
        var expiry = org.mockito.ArgumentCaptor.forClass(Date.class);
        verify(client).generatePresignedUrl(eq("private-bucket"), eq(key), expiry.capture(), eq(HttpMethod.GET));
        assertFalse(expiry.getValue().toInstant().isBefore(before.plusSeconds(299)));
        assertFalse(expiry.getValue().toInstant().isAfter(after.plusSeconds(301)));
        verify(client).shutdown();

        manager.deleteKnowledgeObject(key);
        verify(client).deleteObject("private-bucket", key);
        verify(client, times(2)).shutdown();
        assertThrows(BusinessException.class, () -> manager.deleteKnowledgeObject("community-image/other.png"));
        verify(client, times(2)).shutdown();
    }

    @Test
    void hidesCredentialsAndSignedUrlOnFailure() throws Exception {
        String key = "customer-service-knowledge/0123456789abcdef0123456789abcdef.txt";
        when(client.generatePresignedUrl(eq("private-bucket"), eq(key), any(Date.class), eq(HttpMethod.GET)))
                .thenThrow(new RuntimeException("secret-key-value https://signed.example.test/?token=private"));
        BusinessException error = assertThrows(BusinessException.class, () -> manager.generateKnowledgeDownloadUrl(key));
        assertFalse(error.getMessage().contains("secret-key-value"));
        assertFalse(error.getMessage().contains("signed.example"));
        verify(client).shutdown();
    }

    @Test
    void uploadFailureDoesNotExposeCredentialsOrObjectUrl() {
        when(client.putObject(any(PutObjectRequest.class)))
                .thenThrow(new RuntimeException("secret-key-value https://private-bucket.example.test/object"));
        var file = new MockMultipartFile("file", "guide.txt", "text/plain", "hello".getBytes(StandardCharsets.UTF_8));

        BusinessException error = assertThrows(BusinessException.class, () -> manager.uploadKnowledgeDocument(file));
        assertFalse(error.getMessage().contains("secret-key-value"));
        assertFalse(error.getMessage().contains("private-bucket.example"));
        verify(client).shutdown();
    }
}
