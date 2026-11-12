package com.yupi.yuaicodemother.utils;

import cn.hutool.core.img.ImgUtil;
import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.StrUtil;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import org.openqa.selenium.JavascriptExecutor;
import org.openqa.selenium.OutputType;
import org.openqa.selenium.PageLoadStrategy;
import org.openqa.selenium.TimeoutException;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeDriverService;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.support.ui.WebDriverWait;

import java.io.File;
import java.time.Duration;
import java.util.concurrent.CancellationException;

import static com.yupi.yuaicodemother.utils.ScreenshotTimings.Stage.*;

/** 单次截图独占浏览器，避免虚拟线程结束后留下无法复用的 ThreadLocal 驱动。 */
public class WebScreenshotUtils {
    private static final int DEFAULT_WIDTH = 1600;
    private static final int DEFAULT_HEIGHT = 900;

    public static String saveWebPageScreenshot(String webUrl) {
        return saveWebPageScreenshot(webUrl, null, ScreenshotTimings.newId());
    }

    /** 关联 ID 由截图服务内部生成，成功文件由上传服务清理。 */
    public static String saveWebPageScreenshot(String webUrl, String driverPath, String screenshotId) {
        if (StrUtil.isBlank(webUrl)) return null;
        WebDriver driver = null;
        File directory = null;
        boolean succeeded = false;
        try {
            checkInterrupted();
            long initStarted = System.nanoTime();
            try {
                driver = createDriver(driverPath);
                // 先接管实例再设置超时，配置失败也必须进入 finally 关闭浏览器。
                driver.manage().timeouts().pageLoadTimeout(Duration.ofSeconds(30));
                driver.manage().timeouts().implicitlyWait(Duration.ofSeconds(10));
                ScreenshotTimings.record(screenshotId, DRIVER_INIT, ScreenshotTimings.Result.SUCCESS, initStarted, null);
            } catch (RuntimeException e) {
                ScreenshotTimings.record(screenshotId, DRIVER_INIT, ScreenshotTimings.Result.FAILED, initStarted, e);
                throw e;
            }
            WebDriver owned = driver;
            directory = FileUtil.mkdir(new File(System.getProperty("user.dir"), "tmp/screenshots/" + java.util.UUID.randomUUID()));
            File original = new File(directory, "capture.png");
            File compressed = new File(directory, "capture_compressed.jpg");
            measurePageStage(screenshotId, NAVIGATE, () -> owned.get(webUrl));
            measurePageStage(screenshotId, READY_WAIT, () -> waitForPageLoad(owned));
            checkInterrupted();
            ScreenshotTimings.measure(screenshotId, CAPTURE, () -> {
                FileUtil.writeBytes(((org.openqa.selenium.TakesScreenshot) owned).getScreenshotAs(OutputType.BYTES), original);
                return null;
            });
            checkInterrupted();
            ScreenshotTimings.measure(screenshotId, COMPRESS, () -> {
                ImgUtil.compress(original, compressed, 0.3f);
                return null;
            });
            checkInterrupted();
            FileUtil.del(original);
            succeeded = true;
            return compressed.getAbsolutePath();
        } catch (Exception e) {
            restoreInterrupt(e);
            return null;
        } finally {
            if (driver != null) {
                WebDriver owned = driver;
                try {
                    ScreenshotTimings.measure(screenshotId, DRIVER_CLOSE, () -> { owned.quit(); return null; });
                } catch (Exception e) {
                    // 关闭异常不能覆盖已经取得的图片，但仍保留中断信号。
                    restoreInterrupt(e);
                }
            }
            if (!succeeded && directory != null) {
                File failedDirectory = directory;
                try {
                    ScreenshotTimings.measure(screenshotId, CLEANUP, () -> FileUtil.del(failedDirectory));
                } catch (Exception e) {
                    restoreInterrupt(e);
                }
            }
        }
    }

    private static WebDriver createDriver(String configuredPath) {
        ChromeOptions options = new ChromeOptions();
        options.addArguments("--headless", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                "--window-size=" + DEFAULT_WIDTH + "," + DEFAULT_HEIGHT, "--disable-extensions");
        options.addArguments("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36");
        options.setPageLoadStrategy(PageLoadStrategy.EAGER);
        File executable = resolveDriverPath(configuredPath, System.getProperty("webdriver.chrome.driver"),
                System.getenv("CHROMEDRIVER_PATH"));
        if (executable == null) return new ChromeDriver(options);
        ChromeDriverService service = new ChromeDriverService.Builder().usingDriverExecutable(executable).build();
        return new ChromeDriver(service, options);
    }

    /** 显式配置优先，错误配置失败关闭，不修改 JVM 全局属性或联网猜测替代版本。 */
    static File resolveDriverPath(String configuredPath, String legacyProperty, String environmentPath) {
        String selected = StrUtil.isNotBlank(configuredPath) ? configuredPath
                : StrUtil.isNotBlank(legacyProperty) ? legacyProperty : environmentPath;
        if (StrUtil.isBlank(selected)) return null;
        File executable = new File(selected.trim());
        if (!executable.isFile()) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "chromedriver 显式路径必须为现有普通文件");
        }
        return executable.getAbsoluteFile();
    }

    /** 只对导航和就绪超时保留降级截图，其余异常终止，避免吞掉中断。 */
    private static void measurePageStage(String id, ScreenshotTimings.Stage stage, Runnable action) {
        long started = System.nanoTime();
        try {
            checkInterrupted();
            action.run();
            checkInterrupted();
            ScreenshotTimings.record(id, stage, ScreenshotTimings.Result.SUCCESS, started, null);
        } catch (TimeoutException e) {
            restoreInterrupt(e);
            if (Thread.currentThread().isInterrupted()) {
                ScreenshotTimings.record(id, stage, ScreenshotTimings.Result.FAILED, started, e);
                checkInterrupted();
            }
            ScreenshotTimings.record(id, stage, ScreenshotTimings.Result.DEGRADED, started, e);
        } catch (RuntimeException e) {
            ScreenshotTimings.record(id, stage, ScreenshotTimings.Result.FAILED, started, e);
            throw e;
        }
    }

    private static void waitForPageLoad(WebDriver driver) {
        new WebDriverWait(driver, Duration.ofSeconds(10)).until(current ->
                StrUtil.equalsAnyIgnoreCase(String.valueOf(((JavascriptExecutor) current)
                        .executeScript("return document.readyState")), "interactive", "complete"));
        try {
            Thread.sleep(2000);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new CancellationException("截图已中断");
        }
    }

    private static void checkInterrupted() {
        if (Thread.currentThread().isInterrupted()) throw new CancellationException("截图已中断");
    }

    private static void restoreInterrupt(Throwable failure) {
        for (Throwable cause = failure; cause != null; cause = cause.getCause()) {
            if (cause instanceof InterruptedException) {
                Thread.currentThread().interrupt();
                break;
            }
        }
    }
}
