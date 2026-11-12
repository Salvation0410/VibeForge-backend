package com.yupi.yuaicodemother.service.impl;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.AppenderBase;
import com.yupi.yuaicodemother.service.ScreenshotService;
import org.junit.jupiter.api.Test;
import org.slf4j.LoggerFactory;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.Duration;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

/** 异步封面错误应在任务内收口，不能让部署调用等待截图。 */
class AppScreenshotAsyncTest {
    @Test
    void asyncFailureIsCaughtWithoutBlockingCallerOrLeakingException() throws Exception {
        AppServiceImpl service = new AppServiceImpl(null, null, null, null, null, null);
        ScreenshotService screenshot = mock(ScreenshotService.class);
        ReflectionTestUtils.setField(service, "screenshotService", screenshot);
        CountDownLatch entered = new CountDownLatch(1);
        CountDownLatch release = new CountDownLatch(1);
        CountDownLatch logged = new CountDownLatch(1);
        AtomicReference<ILoggingEvent> event = new AtomicReference<>();
        when(screenshot.generateAndUploadScreenshot(anyString())).thenAnswer(invocation -> {
            entered.countDown();
            assertTrue(release.await(5, TimeUnit.SECONDS));
            throw new IllegalStateException("private-marker");
        });
        Logger logger = (Logger) LoggerFactory.getLogger(AppServiceImpl.class);
        AppenderBase<ILoggingEvent> appender = new AppenderBase<>() {
            @Override protected void append(ILoggingEvent value) {
                event.set(value);
                logged.countDown();
            }
        };
        appender.start();
        logger.addAppender(appender);
        try {
            assertTimeoutPreemptively(Duration.ofSeconds(1), () -> service.generateAppScreenshotAsync(42L, "http://localhost/private-marker"));
            assertTrue(entered.await(3, TimeUnit.SECONDS));
            assertEquals(1, release.getCount());
            release.countDown();
            assertTrue(logged.await(3, TimeUnit.SECONDS));
            assertEquals("screenshot_task appId=42 result=failed exception_type=IllegalStateException", event.get().getFormattedMessage());
            assertNull(event.get().getThrowableProxy());
        } finally {
            release.countDown();
            logger.detachAppender(appender);
        }
    }
}
