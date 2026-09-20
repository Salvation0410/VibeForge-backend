package com.yupi.yuaicodemother.core.artifact;

import lombok.extern.slf4j.Slf4j;
import org.openqa.selenium.JavascriptExecutor;
import org.openqa.selenium.PageLoadStrategy;
import org.openqa.selenium.TimeoutException;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;
import org.openqa.selenium.logging.LogType;
import org.openqa.selenium.logging.LoggingPreferences;
import org.openqa.selenium.support.ui.WebDriverWait;
import org.springframework.stereotype.Component;

import java.nio.file.Path;
import java.nio.file.Files;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.concurrent.FutureTask;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.logging.Level;

/** 使用专用无头 Chrome 对候选 HTML 做有界加载和稳定性观察。 */
@Slf4j
@Component
public class SeleniumHtmlSmokeTester implements HtmlSmokeTester {
    private static final String FAILED = "HTML_SMOKE_TEST_FAILED";
    private static final String UNAVAILABLE = "HTML_SMOKE_TEST_UNAVAILABLE";
    private static final String DRIVER_PROPERTY = "webdriver.chrome.driver";
    private static final String DRIVER_ENV = "CHROMEDRIVER_PATH";
    private static final String ERROR_HOOK = "window.__htmlSmokeErrors=[];"
            + "window.addEventListener('error',e=>{if(e.message)window.__htmlSmokeErrors.push(e.message)});"
            + "window.addEventListener('unhandledrejection',e=>window.__htmlSmokeErrors.push(String(e.reason)));";
    private static final String PAGE_STATE = "const body=document.body;"
            + "if(!body)return {visible:0,meaningful:0,text:'',loading:false,errors:window.__htmlSmokeErrors||[]};"
            + "const isVisible=e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();"
            + "return s.display!=='none'&&s.visibility!=='hidden'&&Number(s.opacity)>0&&r.width>0&&r.height>0};"
            + "const nodes=[...body.querySelectorAll('*')].filter(isVisible);"
            + "const placeholders=[...body.querySelectorAll('.skeleton,.loading,[aria-busy=true],[data-loading]')].filter(isVisible);"
            + "const content=nodes.filter(e=>!placeholders.some(p=>p===e||p.contains(e)));"
            + "const meaningful=content.filter(e=>e.children.length===0&&((e.innerText&&e.innerText.trim())||"
            + "['IMG','SVG','CANVAS','VIDEO','INPUT','BUTTON'].includes(e.tagName)));"
            + "return {visible:nodes.length,meaningful:meaningful.length,text:body.innerText||'',"
            + "loading:placeholders.length>0&&meaningful.length===0,errors:window.__htmlSmokeErrors||[]};";

    /** 在 12 秒截止点返回失败；浏览器未能启动时必须阻止发布，不能把未验证当作通过。 */
    @Override
    public HtmlSmokeTestResult verify(Path indexHtml) {
        AtomicReference<WebDriver> active = new AtomicReference<>();
        FutureTask<HtmlSmokeTestResult> task = new FutureTask<>(() -> inspect(indexHtml, active));
        Thread worker = new Thread(task, "html-smoke-browser");
        worker.setDaemon(true);
        worker.start();
        try {
            return task.get(12, TimeUnit.SECONDS);
        } catch (java.util.concurrent.TimeoutException e) {
            task.cancel(true);
            WebDriver driver = active.get();
            if (driver != null) {
                Thread cleanup = new Thread(() -> { try { driver.quit(); } catch (Exception ignored) { } }, "html-smoke-timeout-cleanup");
                cleanup.setDaemon(true);
                cleanup.start();
            }
            return HtmlSmokeTestResult.failure(driver == null ? UNAVAILABLE : FAILED,
                    driver == null ? "Chrome/WebDriver 启动超过 12 秒" : "HTML 浏览器烟测超过 12 秒");
        } catch (InterruptedException e) {
            task.cancel(true);
            Thread.currentThread().interrupt();
            return HtmlSmokeTestResult.failure(UNAVAILABLE, "HTML 浏览器烟测被中断");
        } catch (Exception e) {
            return HtmlSmokeTestResult.failure(UNAVAILABLE, "HTML 浏览器烟测不可用: " + e.getMessage());
        }
    }

    /** 加载 8 秒以内、观察 3 秒；脚本错误和持续占位是硬失败，资源请求错误仅记录告警。 */
    private HtmlSmokeTestResult inspect(Path indexHtml, AtomicReference<WebDriver> active) {
        WebDriver driver;
        try {
            configureDriverPath();
            ChromeOptions options = new ChromeOptions();
            options.addArguments("--headless=new", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage",
                    "--disable-extensions", "--window-size=1280,800");
            options.setPageLoadStrategy(PageLoadStrategy.EAGER);
            options.setExperimentalOption("prefs", Map.of("profile.default_content_setting_values.popups", 0,
                    "download.prompt_for_download", false));
            LoggingPreferences logs = new LoggingPreferences();
            logs.enable(LogType.BROWSER, Level.ALL);
            options.setCapability("goog:loggingPrefs", logs);
            driver = new ChromeDriver(options);
            active.set(driver);
        } catch (Exception e) {
            log.warn("HTML 烟测浏览器启动失败: {}", e.toString());
            return HtmlSmokeTestResult.failure(UNAVAILABLE, "Chrome/WebDriver 无法启动: " + e.getMessage());
        }
        try {
            ((ChromeDriver) driver).executeCdpCommand("Page.addScriptToEvaluateOnNewDocument", Map.of("source", ERROR_HOOK));
            driver.manage().timeouts().pageLoadTimeout(Duration.ofSeconds(8));
            String expected = indexHtml.toAbsolutePath().toUri().toString();
            driver.get(expected);
            new WebDriverWait(driver, Duration.ofSeconds(8)).until(d ->
                    !"loading".equals(((JavascriptExecutor) d).executeScript("return document.readyState")));
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3);
            do {
                HtmlSmokeTestResult state = check(driver, expected, false);
                if (!state.passed()) return state;
                long remaining = deadline - System.nanoTime();
                if (remaining > 0) Thread.sleep(Math.min(200, Math.max(1, TimeUnit.NANOSECONDS.toMillis(remaining))));
            } while (System.nanoTime() < deadline);
            return check(driver, expected, true);
        } catch (TimeoutException e) {
            return HtmlSmokeTestResult.failure(FAILED, "HTML 主文档加载超过 8 秒");
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return HtmlSmokeTestResult.failure(FAILED, "HTML 烟测超时");
        } catch (Exception e) {
            return HtmlSmokeTestResult.failure(FAILED, "HTML 页面执行失败: " + e.getMessage());
        } finally {
            active.compareAndSet(driver, null);
            try { driver.quit(); } catch (Exception e) { log.debug("关闭烟测浏览器失败", e); }
        }
    }

    /** 显式驱动路径优先于 Selenium Manager；路径无效按环境不可用处理，不偷偷降级。 */
    private void configureDriverPath() {
        if (System.getProperty(DRIVER_PROPERTY) != null && !System.getProperty(DRIVER_PROPERTY).isBlank()) return;
        String configured = System.getenv(DRIVER_ENV);
        if (configured == null || configured.isBlank()) return;
        if (!Files.isRegularFile(Path.of(configured))) throw new IllegalStateException(DRIVER_ENV + " 指向的文件不存在");
        System.setProperty(DRIVER_PROPERTY, configured);
    }

    /** 按主文档 URL、控制台、未捕获异常和最终可见内容判断页面，资源 404 不误判为脚本失败。 */
    @SuppressWarnings("unchecked")
    private HtmlSmokeTestResult check(WebDriver driver, String expected, boolean observationComplete) {
        if (!expected.equals(driver.getCurrentUrl())) {
            return HtmlSmokeTestResult.failure(FAILED, "HTML 页面发生顶层跳转");
        }
        Map<String, Object> state = (Map<String, Object>) ((JavascriptExecutor) driver).executeScript(PAGE_STATE);
        List<String> errors = (List<String>) state.get("errors");
        if (errors != null && !errors.isEmpty()) return HtmlSmokeTestResult.failure(FAILED, "JavaScript 异常: " + errors.getFirst());
        for (var entry : driver.manage().logs().get(LogType.BROWSER)) {
            if (entry.getLevel().intValue() < Level.SEVERE.intValue()) continue;
            String message = entry.getMessage();
            if (message.matches("(?is).*\\b(SyntaxError|Uncaught|ReferenceError|TypeError|RangeError|EvalError)\\b.*")) {
                return HtmlSmokeTestResult.failure(FAILED, "JavaScript 控制台错误: " + message);
            }
            log.warn("HTML 烟测资源告警: {}", message);
        }
        if (((Number) state.get("meaningful")).intValue() == 0 && ((String) state.get("text")).isBlank()) {
            return HtmlSmokeTestResult.failure(FAILED, "HTML 页面没有可见内容");
        }
        if (observationComplete && Boolean.TRUE.equals(state.get("loading"))) {
            return HtmlSmokeTestResult.failure(FAILED, "HTML 页面持续停留在加载占位状态");
        }
        return HtmlSmokeTestResult.success();
    }
}
