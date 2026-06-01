package com.yupi.yuaicodemother.utils;

import cn.hutool.core.img.ImgUtil;
import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.RandomUtil;
import cn.hutool.core.util.StrUtil;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import jakarta.annotation.PreDestroy;
import lombok.extern.slf4j.Slf4j;
import org.openqa.selenium.JavascriptExecutor;
import org.openqa.selenium.OutputType;
import org.openqa.selenium.PageLoadStrategy;
import org.openqa.selenium.TakesScreenshot;
import org.openqa.selenium.TimeoutException;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.support.ui.WebDriverWait;

import java.io.File;
import java.time.Duration;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 网页截图工具类
 *
 * <p>使用 ThreadLocal 为每个线程复用独立的 WebDriver，避免并发线程争用同一个浏览器实例。</p>
 */
@Slf4j
public class WebScreenshotUtils {

    private static final int DEFAULT_WIDTH = 1600;
    private static final int DEFAULT_HEIGHT = 900;
    private static final String CHROME_DRIVER_PATH_PROPERTY = "webdriver.chrome.driver";
    private static final String CHROME_DRIVER_PATH_ENV = "CHROMEDRIVER_PATH";

    /**
     * 每个线程持有自己的 WebDriver，同一线程内重复截图时可复用。
     */
    private static final ThreadLocal<WebDriver> WEB_DRIVER_HOLDER = ThreadLocal.withInitial(
            () -> createAndRegisterWebDriver(DEFAULT_WIDTH, DEFAULT_HEIGHT)
    );

    /**
     * 记录所有已创建的驱动，便于应用退出时统一释放。
     */
    private static final Set<WebDriver> WEB_DRIVER_REGISTRY = ConcurrentHashMap.newKeySet();

    /**
     * 销毁当前进程中已创建的所有 WebDriver 实例
     */
    @PreDestroy
    public void destroy() {
        for (WebDriver driver : WEB_DRIVER_REGISTRY) {
            try {
                driver.quit();
            } catch (Exception e) {
                log.warn("关闭 WebDriver 失败", e);
            }
        }
        WEB_DRIVER_REGISTRY.clear();
        WEB_DRIVER_HOLDER.remove();
    }

    /**
     * 生成网页截图
     *
     * @param webUrl 网页 URL
     * @return 压缩后的截图文件路径，失败返回 null
     */
    public static String saveWebPageScreenshot(String webUrl) {
        if (StrUtil.isBlank(webUrl)) {
            log.error("网页 URL 不能为空");
            return null;
        }
        try {
            WebDriver driver = getOrCreateWebDriver();
            String rootPath = System.getProperty("user.dir") + File.separator + "tmp" + File.separator + "screenshots"
                    + File.separator + UUID.randomUUID().toString().substring(0, 8);
            FileUtil.mkdir(rootPath);

            final String imageSuffix = ".png";
            String imageSavePath = rootPath + File.separator + RandomUtil.randomNumbers(5) + imageSuffix;

            loadPageForScreenshot(driver, webUrl);
            waitForPageLoad(driver);

            byte[] screenshotBytes = ((TakesScreenshot) driver).getScreenshotAs(OutputType.BYTES);
            saveImage(screenshotBytes, imageSavePath);
            log.info("原始截图保存成功: {}", imageSavePath);

            final String compressionSuffix = "_compressed.jpg";
            String compressedImagePath = rootPath + File.separator + RandomUtil.randomNumbers(5) + compressionSuffix;
            compressImage(imageSavePath, compressedImagePath);
            log.info("压缩图片保存成功: {}", compressedImagePath);

            FileUtil.del(imageSavePath);
            return compressedImagePath;
        } catch (Exception e) {
            log.error("网页截图失败: {}", webUrl, e);
            return null;
        }
    }

    private static WebDriver getOrCreateWebDriver() {
        return WEB_DRIVER_HOLDER.get();
    }

    private static WebDriver createAndRegisterWebDriver(int width, int height) {
        WebDriver driver = initChromeDriver(width, height);
        WEB_DRIVER_REGISTRY.add(driver);
        return driver;
    }

    /**
     * 初始化 Chrome 浏览器驱动
     */
    private static WebDriver initChromeDriver(int width, int height) {
        try {
            ChromeOptions options = new ChromeOptions();
            options.addArguments("--headless");
            options.addArguments("--disable-gpu");
            options.addArguments("--no-sandbox");
            options.addArguments("--disable-dev-shm-usage");
            options.addArguments(String.format("--window-size=%d,%d", width, height));
            options.addArguments("--disable-extensions");
            options.addArguments("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36");
            options.setPageLoadStrategy(PageLoadStrategy.EAGER);

            configureChromeDriverPath();

            WebDriver driver = new ChromeDriver(options);
            driver.manage().timeouts().pageLoadTimeout(Duration.ofSeconds(30));
            driver.manage().timeouts().implicitlyWait(Duration.ofSeconds(10));
            return driver;
        } catch (Exception e) {
            log.error("初始化 Chrome 浏览器失败", e);
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "初始化 Chrome 浏览器失败");
        }
    }

    /**
     * 截图场景只要求页面主体可见，不强依赖所有资源完全加载完成。
     */
    private static void loadPageForScreenshot(WebDriver driver, String webUrl) {
        try {
            driver.get(webUrl);
        } catch (TimeoutException e) {
            log.warn("页面加载超时，继续尝试截图: {}", webUrl, e);
        }
    }

    private static void configureChromeDriverPath() {
        String configuredPath = System.getProperty(CHROME_DRIVER_PATH_PROPERTY);
        if (StrUtil.isBlank(configuredPath)) {
            configuredPath = System.getenv(CHROME_DRIVER_PATH_ENV);
        }
        if (StrUtil.isBlank(configuredPath)) {
            log.info("未显式配置 chromedriver 路径，将使用 Selenium 默认驱动解析机制");
            return;
        }
        if (!FileUtil.exist(configuredPath)) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR,
                    "chromedriver 路径不存在，请检查 " + CHROME_DRIVER_PATH_PROPERTY + " 或 " + CHROME_DRIVER_PATH_ENV);
        }
        System.setProperty(CHROME_DRIVER_PATH_PROPERTY, configuredPath);
        log.info("使用本地 chromedriver: {}", configuredPath);
    }

    /**
     * 保存图片到文件
     */
    private static void saveImage(byte[] imageBytes, String imagePath) {
        try {
            FileUtil.writeBytes(imageBytes, imagePath);
        } catch (Exception e) {
            log.error("保存图片失败: {}", imagePath, e);
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "保存图片失败");
        }
    }

    /**
     * 压缩图片
     */
    private static void compressImage(String originalImagePath, String compressedImagePath) {
        final float compressionQuality = 0.3f;
        try {
            ImgUtil.compress(
                    FileUtil.file(originalImagePath),
                    FileUtil.file(compressedImagePath),
                    compressionQuality
            );
        } catch (Exception e) {
            log.error("压缩图片失败: {} -> {}", originalImagePath, compressedImagePath, e);
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "压缩图片失败");
        }
    }

    /**
     * 等待页面加载完成
     */
    private static void waitForPageLoad(WebDriver driver) {
        try {
            WebDriverWait wait = new WebDriverWait(driver, Duration.ofSeconds(10));
            wait.until(currentDriver ->
                    StrUtil.equalsAnyIgnoreCase(
                            String.valueOf(((JavascriptExecutor) currentDriver).executeScript("return document.readyState")),
                            "interactive",
                            "complete"
                    )
            );
            Thread.sleep(2000);
            log.info("页面加载完成");
        } catch (TimeoutException e) {
            log.warn("等待页面加载超时，继续执行截图");
        } catch (Exception e) {
            log.warn("等待页面加载时出现异常，继续执行截图: {}", e.getMessage());
        }
    }
}
