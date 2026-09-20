package com.yupi.yuaicodemother.core.artifact;

import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Tag;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

@Tag("browser")
class SeleniumHtmlSmokeTesterTest {
    @TempDir Path root;
    private final SeleniumHtmlSmokeTester tester = new SeleniumHtmlSmokeTester();

    @BeforeAll
    static void chromeAvailable() throws Exception {
        Path probe = Files.createTempFile("html-smoke-probe", ".html");
        try {
            Files.writeString(probe, "<!doctype html><html><body>Browser probe</body></html>");
            HtmlSmokeTestResult result = new SeleniumHtmlSmokeTester().verify(probe);
            assumeTrue(!"HTML_SMOKE_TEST_UNAVAILABLE".equals(result.errorCode()),
                    () -> "浏览器环境不可用: " + result.message());
            assertTrue(result.passed(), () -> "浏览器探针失败: " + result);
        } finally {
            Files.deleteIfExists(probe);
        }
    }

    @Test void validPagePasses() throws Exception {
        assertTrue(tester.verify(page("<main>Ready application</main>")).passed());
    }

    @Test void syntaxErrorFails() throws Exception {
        assertEquals("HTML_SMOKE_TEST_FAILED", tester.verify(page(
                "<main>Ready application</main><script>const value = ;</script>")).errorCode());
    }

    @Test void permanentSkeletonFails() throws Exception {
        assertEquals("HTML_SMOKE_TEST_FAILED", tester.verify(page(
                "<div class='skeleton'>Loading application...</div>")).errorCode());
    }

    @Test void delayedRenderingPassesAfterObservationStarts() throws Exception {
        assertTrue(tester.verify(page("<script>setTimeout(()=>document.body.innerHTML='<main>Ready later</main>',1000)</script>")).passed());
    }

    @Test void permanentlyBlankPageFailsAfterObservation() throws Exception {
        assertEquals("HTML_SMOKE_TEST_FAILED", tester.verify(page("")).errorCode());
    }

    @Test void externalImageFailureIsWarning() throws Exception {
        assertTrue(tester.verify(page("<main>Ready application</main><img src='https://127.0.0.1:1/missing.png'>")).passed());
    }

    @Test void topLevelNavigationFails() throws Exception {
        assertEquals("HTML_SMOKE_TEST_FAILED", tester.verify(page(
                "<main>Ready application</main><script>location.href='about:blank'</script>")).errorCode());
    }

    private Path page(String body) throws Exception {
        Path file = root.resolve("index.html");
        Files.writeString(file, "<!doctype html><html><head></head><body>" + body + "</body></html>");
        return file;
    }
}
