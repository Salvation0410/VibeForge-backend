package com.yupi.yuaicodemother.utils;

import cn.hutool.core.io.FileUtil;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.io.TempDir;
import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import org.slf4j.LoggerFactory;
import org.mockito.MockedConstruction;
import org.openqa.selenium.OutputType;
import org.openqa.selenium.TimeoutException;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeDriverService;
import org.openqa.selenium.WebDriverException;

import javax.imageio.ImageIO;
import java.awt.image.BufferedImage;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.*;

/** 离线验证浏览器所有权，不依赖 Spring、网络或真实浏览器。 */
class WebScreenshotUtilsTest {
    @TempDir Path temp;
    private String previousDirectory;
    private String previousDriver;

    @BeforeEach
    void isolateOutput() {
        previousDirectory = System.getProperty("user.dir");
        previousDriver = System.getProperty("webdriver.chrome.driver");
        System.setProperty("user.dir", temp.toString());
        System.clearProperty("webdriver.chrome.driver");
    }

    @AfterEach
    void restoreProperties() {
        System.setProperty("user.dir", previousDirectory);
        if (previousDriver == null) System.clearProperty("webdriver.chrome.driver");
        else System.setProperty("webdriver.chrome.driver", previousDriver);
        Thread.interrupted();
    }
    private static byte[] png() throws Exception {
        ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        ImageIO.write(new BufferedImage(8, 8, BufferedImage.TYPE_INT_RGB), "png", bytes);
        return bytes.toByteArray();
    }

    private static void prepare(ChromeDriver driver) throws Exception {
        WebDriver.Options options = mock(WebDriver.Options.class);
        WebDriver.Timeouts timeouts = mock(WebDriver.Timeouts.class);
        when(driver.manage()).thenReturn(options);
        when(options.timeouts()).thenReturn(timeouts);
        when(driver.executeScript(anyString())).thenThrow(new TimeoutException("private-marker"));
        when(driver.getScreenshotAs(OutputType.BYTES)).thenReturn(png());
    }

    @Test
    void successfulCaptureClosesOwnedDriver() {
        try (MockedConstruction<ChromeDriver> drivers = mockConstruction(ChromeDriver.class,
                (driver, context) -> prepare(driver))) {
            String result = WebScreenshotUtils.saveWebPageScreenshot("http://localhost/private-marker");
            try {
                assertNotNull(result);
                assertTrue(new File(result).isFile());
                verify(drivers.constructed().getFirst()).quit();
            } finally {
                if (result != null) FileUtil.del(new File(result).getParentFile());
            }
        }
    }

    @Test
    void timeoutSetupFailureStillClosesDriver() {
        try (MockedConstruction<ChromeDriver> drivers = mockConstruction(ChromeDriver.class,
                (driver, context) -> {
                    prepare(driver);
                    when(driver.manage().timeouts().pageLoadTimeout(any())).thenThrow(new IllegalStateException("private-marker"));
                })) {
            assertNull(WebScreenshotUtils.saveWebPageScreenshot("http://localhost/private-marker"));
            verify(drivers.constructed().getFirst()).quit();
        }
    }

    @Test
    void captureFailureClosesDriverAndRemovesOnlyItsDirectory() throws Exception {
        Path unrelated = Files.createDirectories(temp.resolve("tmp/screenshots/unrelated"));
        Files.writeString(unrelated.resolve("keep.txt"), "keep");
        try (MockedConstruction<ChromeDriver> drivers = mockConstruction(ChromeDriver.class, (driver, context) -> {
            prepare(driver);
            when(driver.getScreenshotAs(OutputType.BYTES)).thenThrow(new WebDriverException("private-marker"));
        })) {
            assertNull(WebScreenshotUtils.saveWebPageScreenshot("http://localhost/private-marker"));
            verify(drivers.constructed().getFirst()).quit();
            try (var directories = Files.list(unrelated.getParent())) {
                assertEquals(List.of(unrelated), directories.toList());
            }
        }
    }

    @Test
    void compressionFailureClosesDriverAndCleansPartialImage() throws Exception {
        try (MockedConstruction<ChromeDriver> drivers = mockConstruction(ChromeDriver.class, (driver, context) -> {
            prepare(driver);
            when(driver.getScreenshotAs(OutputType.BYTES)).thenReturn(new byte[]{1, 2, 3});
        })) {
            assertNull(WebScreenshotUtils.saveWebPageScreenshot("http://localhost/private-marker"));
            verify(drivers.constructed().getFirst()).quit();
            try (var directories = Files.list(temp.resolve("tmp/screenshots"))) {
                assertEquals(0, directories.count());
            }
        }
    }

    @Test
    void closeFailureDoesNotDiscardSuccessfulScreenshot() {
        try (MockedConstruction<ChromeDriver> drivers = mockConstruction(ChromeDriver.class, (driver, context) -> {
            prepare(driver);
            doThrow(new WebDriverException("private-marker")).when(driver).quit();
        })) {
            String output = WebScreenshotUtils.saveWebPageScreenshot("http://localhost/private-marker");
            assertNotNull(output);
            assertTrue(new File(output).isFile());
            verify(drivers.constructed().getFirst()).quit();
        }
    }

    @Test
    void navigationTimeoutIsDegradedAndLogsNeverExposeInputsOrExceptionBody() {
        Logger logger = (Logger) LoggerFactory.getLogger(ScreenshotTimings.class);
        ListAppender<ILoggingEvent> appender = new ListAppender<>();
        appender.start();
        logger.addAppender(appender);
        try (MockedConstruction<ChromeDriver> drivers = mockConstruction(ChromeDriver.class, (driver, context) -> {
            prepare(driver);
            doThrow(new TimeoutException("private-marker")).when(driver).get(anyString());
        })) {
            assertNotNull(WebScreenshotUtils.saveWebPageScreenshot("http://localhost/private-marker", null, "internal-test-id"));
            List<String> logs = appender.list.stream().map(ILoggingEvent::getFormattedMessage).toList();
            assertTrue(logs.stream().anyMatch(log -> log.contains("stage=navigate result=degraded")));
            assertTrue(logs.stream().anyMatch(log -> log.contains("stage=driver_close result=success")));
            assertTrue(logs.stream().allMatch(log -> log.contains("id=internal-test-id") && !log.contains("private-marker")
                    && !log.contains("http") && !log.contains(temp.toString())));
            assertTrue(appender.list.stream().allMatch(event -> event.getThrowableProxy() == null));
        } finally {
            logger.detachAppender(appender);
        }
    }

    @Test
    void interruptedReadyWaitAbortsCaptureAndRestoresInterrupt() {
        try (MockedConstruction<ChromeDriver> drivers = mockConstruction(ChromeDriver.class, (driver, context) -> {
            prepare(driver);
            doAnswer(invocation -> { Thread.currentThread().interrupt(); return "complete"; })
                    .when(driver).executeScript(anyString());
        })) {
            assertNull(WebScreenshotUtils.saveWebPageScreenshot("http://localhost/private-marker"));
            assertTrue(Thread.currentThread().isInterrupted());
            verify(drivers.constructed().getFirst(), never()).getScreenshotAs(any());
            verify(drivers.constructed().getFirst()).quit();
        }
    }

    @Test
    void configuredPathUsesServiceWithoutChangingLegacyProperty() throws Exception {
        Path explicit = Files.createFile(temp.resolve("explicit-driver"));
        System.setProperty("webdriver.chrome.driver", temp.resolve("invalid-legacy").toString());
        AtomicReference<List<?>> arguments = new AtomicReference<>();
        try (MockedConstruction<ChromeDriver> drivers = mockConstruction(ChromeDriver.class, (driver, context) -> {
            arguments.set(context.arguments());
            prepare(driver);
        })) {
            assertNotNull(WebScreenshotUtils.saveWebPageScreenshot("http://localhost/", explicit.toString(), "test"));
            assertInstanceOf(ChromeDriverService.class, arguments.get().getFirst());
            assertEquals(temp.resolve("invalid-legacy").toString(), System.getProperty("webdriver.chrome.driver"));
        }
    }

    @Test
    void driverPathPrecedenceAndDefaultResolution() throws Exception {
        Path configured = Files.createFile(temp.resolve("configured"));
        Path legacy = Files.createFile(temp.resolve("legacy"));
        Path environment = Files.createFile(temp.resolve("environment"));
        assertEquals(configured.toFile(), WebScreenshotUtils.resolveDriverPath(configured.toString(), legacy.toString(), environment.toString()));
        assertEquals(legacy.toFile(), WebScreenshotUtils.resolveDriverPath(" ", legacy.toString(), environment.toString()));
        assertEquals(environment.toFile(), WebScreenshotUtils.resolveDriverPath(null, "", environment.toString()));
        assertNull(WebScreenshotUtils.resolveDriverPath(null, " ", null));
    }

    @Test
    void invalidExplicitPathDoesNotCreateDriverOrFallBack() {
        try (MockedConstruction<ChromeDriver> drivers = mockConstruction(ChromeDriver.class)) {
            assertNull(WebScreenshotUtils.saveWebPageScreenshot("http://localhost/", temp.resolve("missing").toString(), "test"));
            assertNull(WebScreenshotUtils.saveWebPageScreenshot("http://localhost/", temp.toString(), "test"));
            assertTrue(drivers.constructed().isEmpty());
            assertThrows(RuntimeException.class, () -> WebScreenshotUtils.resolveDriverPath("missing", temp.toString(), null));
        }
    }
}
