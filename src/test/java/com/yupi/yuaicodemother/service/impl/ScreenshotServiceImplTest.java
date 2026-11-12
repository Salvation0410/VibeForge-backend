package com.yupi.yuaicodemother.service.impl;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import com.aliyun.oss.OSS;
import com.aliyun.oss.OSSClientBuilder;
import com.aliyun.oss.model.PutObjectRequest;
import com.yupi.yuaicodemother.config.OssProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.utils.ScreenshotTimings;
import com.yupi.yuaicodemother.utils.WebScreenshotUtils;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.MockedConstruction;
import org.mockito.MockedStatic;
import org.mockito.ArgumentCaptor;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** 上传边界完全使用本地文件和 OSS 替身，不连接外网或读取实际密钥。 */
class ScreenshotServiceImplTest {
    @TempDir Path temp;

    private OssProperties properties() {
        OssProperties properties = new OssProperties();
        properties.setBucketName("private-marker");
        properties.setAccessKeyId("private-marker");
        properties.setAccessKeySecret("private-marker");
        properties.setEndpoint("https://private-marker.invalid");
        properties.setWebSite("https://private-marker.invalid");
        return properties;
    }

    @Test
    void successfulUploadPassesConfiguredPathAndSharesIdWithCleanup() throws Exception {
        runUpload(false);
    }

    @Test
    void failedUploadStillClosesOssAndCleansLocalDirectory() throws Exception {
        runUpload(true);
    }

    private void runUpload(boolean fail) throws Exception {
        Path directory = Files.createDirectory(temp.resolve("private-marker"));
        Path file = Files.write(directory.resolve("capture.jpg"), new byte[]{1, 2, 3});
        ScreenshotServiceImpl service = new ScreenshotServiceImpl(properties());
        ReflectionTestUtils.setField(service, "driverPath", "private-driver-marker");
        OSS client = mock(OSS.class);
        if (fail) when(client.putObject(any(PutObjectRequest.class))).thenThrow(new IllegalStateException("private-marker"));
        AtomicReference<String> screenshotId = new AtomicReference<>();
        Logger logger = (Logger) LoggerFactory.getLogger(ScreenshotTimings.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try (MockedStatic<WebScreenshotUtils> screenshot = mockStatic(WebScreenshotUtils.class);
             MockedConstruction<OSSClientBuilder> builders = mockConstruction(OSSClientBuilder.class, (builder, context) ->
                     when(builder.build(anyString(), anyString(), anyString())).thenReturn(client))) {
            screenshot.when(() -> WebScreenshotUtils.saveWebPageScreenshot(anyString(), eq("private-driver-marker"), anyString()))
                    .thenAnswer(invocation -> {
                        screenshotId.set(invocation.getArgument(2));
                        return file.toString();
                    });
            if (fail) assertThrows(BusinessException.class, () -> service.generateAndUploadScreenshot("http://localhost/private-marker"));
            else assertTrue(service.generateAndUploadScreenshot("http://localhost/private-marker").startsWith("https://private-marker.invalid/screenshots/"));
            verify(client).putObject(any(PutObjectRequest.class));
            verify(client).shutdown();
            assertFalse(Files.exists(directory));
            assertNotNull(screenshotId.get());
            var messages = appender.list.stream().map(ILoggingEvent::getFormattedMessage).toList();
            assertTrue(messages.stream().anyMatch(message -> message.contains("stage=upload result=" + (fail ? "failed" : "success"))));
            assertTrue(messages.stream().anyMatch(message -> message.contains("stage=cleanup result=success")));
            assertTrue(messages.stream().allMatch(message -> message.contains("id=" + screenshotId.get()) && !message.contains("private-marker")
                    && !message.contains("private-driver-marker") && !message.contains("https://")));
            assertTrue(appender.list.stream().allMatch(event -> event.getThrowableProxy() == null));
        } finally {
            logger.detachAppender(appender);
        }
    }

    @Test
    void missingOssConfigurationStillCleansGeneratedFile() throws Exception {
        Path directory = Files.createDirectory(temp.resolve("output"));
        Path file = Files.write(directory.resolve("capture.jpg"), new byte[]{1});
        ScreenshotServiceImpl service = new ScreenshotServiceImpl(new OssProperties());
        try (MockedStatic<WebScreenshotUtils> screenshot = mockStatic(WebScreenshotUtils.class)) {
            screenshot.when(() -> WebScreenshotUtils.saveWebPageScreenshot(anyString(), isNull(), anyString())).thenReturn(file.toString());
            assertThrows(BusinessException.class, () -> service.generateAndUploadScreenshot("http://localhost/"));
            assertFalse(Files.exists(directory));
        }
    }

    @Test
    void sameNamedImagesReceiveDistinctOssKeys() throws Exception {
        Path firstDirectory = Files.createDirectory(temp.resolve("first"));
        Path secondDirectory = Files.createDirectory(temp.resolve("second"));
        Path firstFile = Files.write(firstDirectory.resolve("capture_compressed.jpg"), new byte[]{1});
        Path secondFile = Files.write(secondDirectory.resolve("capture_compressed.jpg"), new byte[]{2});
        OSS client = mock(OSS.class);
        ScreenshotServiceImpl service = new ScreenshotServiceImpl(properties());
        try (MockedStatic<WebScreenshotUtils> screenshot = mockStatic(WebScreenshotUtils.class);
             MockedConstruction<OSSClientBuilder> builders = mockConstruction(OSSClientBuilder.class, (builder, context) ->
                     when(builder.build(anyString(), anyString(), anyString())).thenReturn(client))) {
            screenshot.when(() -> WebScreenshotUtils.saveWebPageScreenshot(anyString(), isNull(), anyString()))
                    .thenReturn(firstFile.toString(), secondFile.toString());
            String firstUrl = service.generateAndUploadScreenshot("http://localhost/first");
            String secondUrl = service.generateAndUploadScreenshot("http://localhost/second");
            ArgumentCaptor<PutObjectRequest> requests = ArgumentCaptor.forClass(PutObjectRequest.class);
            verify(client, times(2)).putObject(requests.capture());
            assertNotEquals(requests.getAllValues().get(0).getKey(), requests.getAllValues().get(1).getKey());
            assertNotEquals(firstUrl, secondUrl);
            assertFalse(Files.exists(firstDirectory));
            assertFalse(Files.exists(secondDirectory));
        }
    }
}
