package com.yupi.yuaicodemother.core.builder;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;

class VueAnchorCheckTest {
    static final String OK = "{\"version\":1,\"status\":\"ok\",\"checkedLinks\":1,\"skippedLinks\":0,\"truncated\":false,\"reason\":\"NONE\",\"issues\":[]}";
    private static final String COMPONENT_OK = "{\"version\":1,\"status\":\"ok\",\"checkedComponents\":1,\"checkedUsages\":1,\"skippedFiles\":0,\"skippedUsages\":0,\"truncated\":false,\"issues\":[]}";
    @TempDir Path directory;

    private static String missing() {
        return OK.replace("\"ok\"", "\"failed\"").replace("[]", "[{\"file\":\"src/Hero.vue\",\"target\":\"series\"}]");
    }

    @Test
    void parsesMissingTargetAndExplicitCoverageLimits() {
        var failure = VueAnchorCheck.parse(missing());
        assertEquals("VUE_ANCHOR_TARGET_MISSING", failure.errorCode());
        assertTrue(failure.message().contains("src/Hero.vue -> #series"));
        assertTrue(VueAnchorCheck.parse(OK).built());
        var skipped = VueAnchorCheck.parse(OK.replace("\"NONE\"", "\"DYNAMIC_PAGE\"").replace("\"checkedLinks\":1", "\"checkedLinks\":0"));
        assertTrue(skipped.built());
        assertTrue(skipped.message().contains("跳过"));
    }

    @Test
    void rejectsMalformedUnboundedAndContradictoryProtocol() {
        for (String value : List.of("", "{}", OK + " {}", missing().replace("src/Hero.vue", "../Hero.vue"),
                missing().replace("series", "<script>secret</script>"), missing().replace("\"NONE\"", "\"DYNAMIC_PAGE\""),
                OK.replace("\"version\":1", "\"version\":1,\"version\":1"),
                OK.replace("\"checkedLinks\":1", "\"checkedLinks\":-1"),
                OK.replace("\"NONE\"", "\"private-reason\""),
                missing().replace("\"truncated\":false", "\"truncated\":true"))) {
            var result = VueAnchorCheck.parse(value);
            assertEquals("VUE_ANCHOR_CHECK_UNAVAILABLE", result.errorCode());
            assertFalse(result.message().contains("secret"));
        }
    }

    @Test
    void forcedBuildRunsAnchorCheckAfterComponentCheckAndRejectsMissingTarget() throws Exception {
        prepare();
        var commands = new java.util.ArrayList<List<String>>();
        var builder = new VueProjectBuilder((cwd, command, timeout) -> {
            commands.add(command);
            return new VueProjectBuilder.CommandResult(0, command.getFirst().equals("node")
                    ? command.contains("--anchors") ? missing() : COMPONENT_OK : "ok", "");
        });
        assertEquals("VUE_ANCHOR_TARGET_MISSING", builder.buildProjectDetailed(directory.toString()).errorCode());
        assertEquals(4, commands.size());
        assertTrue(commands.getLast().contains("--anchors"));
        // 已有 dist 的兼容快路径仍不重检，不能冒充重新验收成功。
        assertTrue(builder.ensureProjectBuiltDetailed(directory.toString()).built());
        assertEquals(4, commands.size());
    }

    @Test
    void cancellationDuringAnchorCheckUsesOriginalProcessCleanup() throws Exception {
        prepare();
        var checking = new CountDownLatch(1);
        var builder = new VueProjectBuilder((cwd, command, timeout) -> {
            if (command.contains("--anchors")) {
                checking.countDown();
                new CountDownLatch(1).await();
            }
            return new VueProjectBuilder.CommandResult(0, command.getFirst().equals("node") ? COMPONENT_OK : "ok", "");
        });
        var task = CompletableFuture.supplyAsync(() -> builder.buildProjectDetailed(directory.toString(), "anchor-cancel"));
        assertTrue(checking.await(2, TimeUnit.SECONDS));
        assertTrue(builder.cancelBuild("anchor-cancel"));
        assertEquals("VUE_BUILD_CANCELLED", task.get(2, TimeUnit.SECONDS).errorCode());
    }

    @Test
    void checkerFailureDoesNotExposeOutputOrLeakTemporaryScript() throws Exception {
        Path[] script = new Path[1];
        var result = VueComponentContractCheck.verifyAnchors(directory.toFile(), (cwd, command, timeout) -> {
            script[0] = Path.of(command.get(1));
            assertTrue(Files.isRegularFile(script[0]));
            return new VueProjectBuilder.CommandResult(1, "private-source", "private-error");
        });
        assertEquals("VUE_ANCHOR_CHECK_UNAVAILABLE", result.errorCode());
        assertFalse(result.message().contains("private"));
        assertFalse(Files.exists(script[0]));
    }

    private void prepare() throws Exception {
        Files.writeString(directory.resolve("package.json"), "{}");
        Files.createDirectories(directory.resolve("dist"));
        Files.writeString(directory.resolve("dist/index.html"), "<html></html>");
    }
}
