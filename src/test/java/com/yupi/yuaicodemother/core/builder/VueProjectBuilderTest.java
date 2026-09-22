package com.yupi.yuaicodemother.core.builder;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertTimeoutPreemptively;

class VueProjectBuilderTest {

    @TempDir
    Path tempDir;

    @Test
    void reportsMissingPackageJsonWithoutRunningCommands() {
        List<List<String>> commands = new ArrayList<>();
        var builder = new VueProjectBuilder((workingDirectory, command, timeoutSeconds) -> {
            commands.add(command);
            return new VueProjectBuilder.CommandResult(0, "", "");
        });

        VueBuildResult result = builder.ensureProjectBuiltDetailed(tempDir.toString());

        assertFalse(result.built());
        assertEquals("VUE_PACKAGE_JSON_MISSING", result.errorCode());
        assertEquals("package.json is missing", result.message());
        assertTrue(commands.isEmpty());
    }

    @Test
    void reportsSanitizedBoundedInstallFailure() throws Exception {
        Files.writeString(tempDir.resolve("package.json"), "{}");
        String environmentValue = System.getenv().values().stream()
                .filter(value -> value != null && value.length() >= 8)
                .findFirst()
                .orElse("sensitive-environment-value");
        String commandOutput = tempDir.toRealPath() + "\n" + environmentValue + "\n" + "x".repeat(5000);
        var builder = new VueProjectBuilder((workingDirectory, command, timeoutSeconds) ->
                new VueProjectBuilder.CommandResult(17, "", commandOutput));

        VueBuildResult result = builder.ensureProjectBuiltDetailed(tempDir.toString());

        assertFalse(result.built());
        assertEquals("VUE_NPM_INSTALL_FAILED", result.errorCode());
        assertTrue(result.message().length() <= 4000);
        assertFalse(result.message().contains(tempDir.toRealPath().toString()));
        assertFalse(result.message().contains(environmentValue));
    }

    @Test
    void sanitizesForwardSlashProjectPathIgnoringWindowsCase() throws Exception {
        Files.writeString(tempDir.resolve("package.json"), "{}");
        String forwardSlashRoot = tempDir.toRealPath().toString()
                .replace('\\', '/')
                .toLowerCase(Locale.ROOT);
        var builder = new VueProjectBuilder((workingDirectory, command, timeoutSeconds) ->
                new VueProjectBuilder.CommandResult(
                        1,
                        "",
                        "Rollup failed at " + forwardSlashRoot + "/src/App.vue"));

        VueBuildResult result = builder.ensureProjectBuiltDetailed(tempDir.toString());

        assertEquals("VUE_NPM_INSTALL_FAILED", result.errorCode());
        assertFalse(result.message().toLowerCase(Locale.ROOT).contains(forwardSlashRoot));
    }

    @Test
    void reportsBuildCommandFailure() throws Exception {
        Files.writeString(tempDir.resolve("package.json"), "{}");
        var builder = new VueProjectBuilder((workingDirectory, command, timeoutSeconds) ->
                command.contains("install")
                        ? new VueProjectBuilder.CommandResult(0, "installed", "")
                        : new VueProjectBuilder.CommandResult(2, "", "vite failed"));

        VueBuildResult result = builder.ensureProjectBuiltDetailed(tempDir.toString());

        assertFalse(result.built());
        assertEquals("VUE_NPM_BUILD_FAILED", result.errorCode());
        assertTrue(result.message().contains("vite failed"));
    }

    @Test
    void reportsMissingDistIndexAfterSuccessfulCommands() throws Exception {
        Files.writeString(tempDir.resolve("package.json"), "{}");
        var builder = new VueProjectBuilder((workingDirectory, command, timeoutSeconds) ->
                new VueProjectBuilder.CommandResult(0, "ok", ""));

        VueBuildResult result = builder.ensureProjectBuiltDetailed(tempDir.toString());

        assertFalse(result.built());
        assertEquals("VUE_DIST_INDEX_MISSING", result.errorCode());
        assertEquals("dist/index.html was not generated", result.message());
    }

    @Test
    void forceBuildUsesCurrentCommandResultEvenWhenOldDistIsReady() throws Exception {
        Files.writeString(tempDir.resolve("package.json"), "{}");
        Files.createDirectories(tempDir.resolve("dist"));
        Files.writeString(tempDir.resolve("dist/index.html"), "old preview");
        List<List<String>> commands = new ArrayList<>();
        var builder = new VueProjectBuilder((workingDirectory, command, timeoutSeconds) -> {
            commands.add(command);
            return new VueProjectBuilder.CommandResult(1, "", "current install failed");
        });

        VueBuildResult forced = builder.buildProjectDetailed(tempDir.toString());

        assertEquals("VUE_NPM_INSTALL_FAILED", forced.errorCode());
        assertEquals(1, commands.size());
        assertTrue(Files.exists(tempDir.resolve("dist/index.html")));
        assertTrue(builder.ensureProjectBuiltDetailed(tempDir.toString()).built());
        assertEquals(1, commands.size());
    }

    @Test
    void failureMessageKeepsOperationHeaderAndFinalDiagnostic() throws Exception {
        Files.writeString(tempDir.resolve("package.json"), "{}");
        String output = "initial diagnostic\n" + "x".repeat(12_000) + "\nTAIL_SENTINEL";
        VueProjectBuilder.BoundedOutput boundedOutput = new VueProjectBuilder.BoundedOutput();
        char[] outputChars = output.toCharArray();
        boundedOutput.append(outputChars, 0, outputChars.length);
        var builder = new VueProjectBuilder((workingDirectory, command, timeoutSeconds) ->
                new VueProjectBuilder.CommandResult(23, "", boundedOutput.value()));

        VueBuildResult result = builder.buildProjectDetailed(tempDir.toString());

        assertTrue(boundedOutput.value().startsWith("initial diagnostic"));
        assertTrue(boundedOutput.value().contains("TAIL_SENTINEL"));
        assertTrue(result.message().startsWith("npm install failed (exit code 23)"));
        assertTrue(result.message().contains("TAIL_SENTINEL"));
        assertTrue(result.message().length() <= 4000);
    }

    @Test
    void incompleteOutputFutureIsCancelledWithinBoundedWait() {
        CompletableFuture<String> output = new CompletableFuture<>();

        VueProjectBuilder.OutputCapture capture = assertTimeoutPreemptively(
                Duration.ofSeconds(1),
                () -> VueProjectBuilder.awaitOutput(output, 10, TimeUnit.MILLISECONDS));

        assertTrue(capture.timedOut());
        assertTrue(output.isCancelled());
    }

    @Test
    void concurrentForceBuildWaitsThenRunsItsOwnBuild() throws Exception {
        Files.writeString(tempDir.resolve("package.json"), "{}");
        CountDownLatch firstInstallStarted = new CountDownLatch(1);
        CountDownLatch releaseFirstInstall = new CountDownLatch(1);
        CountDownLatch secondCallEntered = new CountDownLatch(1);
        CountDownLatch secondInstallStarted = new CountDownLatch(1);
        AtomicInteger installCount = new AtomicInteger();
        var builder = new VueProjectBuilder((workingDirectory, command, timeoutSeconds) -> {
            if (command.contains("install")) {
                int invocation = installCount.incrementAndGet();
                if (invocation == 1) {
                    firstInstallStarted.countDown();
                    assertTrue(releaseFirstInstall.await(2, TimeUnit.SECONDS));
                } else if (invocation == 2) {
                    secondInstallStarted.countDown();
                }
            } else {
                Files.createDirectories(tempDir.resolve("dist"));
                Files.writeString(tempDir.resolve("dist/index.html"), "current build");
            }
            return new VueProjectBuilder.CommandResult(0, "ok", "");
        });

        CompletableFuture<VueBuildResult> first = CompletableFuture.supplyAsync(
                () -> builder.buildProjectDetailed(tempDir.toString()));
        assertTrue(firstInstallStarted.await(2, TimeUnit.SECONDS));
        CompletableFuture<VueBuildResult> second = CompletableFuture.supplyAsync(() -> {
            secondCallEntered.countDown();
            return builder.buildProjectDetailed(tempDir.toString());
        });
        assertTrue(secondCallEntered.await(2, TimeUnit.SECONDS));
        assertFalse(secondInstallStarted.await(100, TimeUnit.MILLISECONDS));

        releaseFirstInstall.countDown();

        assertTrue(first.get(2, TimeUnit.SECONDS).built());
        assertTrue(secondInstallStarted.await(2, TimeUnit.SECONDS));
        assertTrue(second.get(2, TimeUnit.SECONDS).built());
        assertEquals(2, installCount.get());
    }

    @ParameterizedTest
    @ValueSource(ints = {4001, 8000})
    void boundedOutputKeepsContentAtOrBelowLimitWithoutMarker(int length) {
        String content = "x".repeat(length);
        VueProjectBuilder.BoundedOutput output = boundedOutput(content);

        assertEquals(content, output.value());
        assertFalse(output.value().contains("...[truncated]..."));
    }

    @Test
    void boundedOutputTruncatesAtFirstByteOverLimitWithinBudget() {
        String content = "HEAD" + "x".repeat(7993) + "TAIL";
        VueProjectBuilder.BoundedOutput output = boundedOutput(content);

        assertTrue(output.value().startsWith("HEAD"));
        assertTrue(output.value().endsWith("TAIL"));
        assertTrue(output.value().contains("...[truncated]..."));
        assertTrue(output.value().length() <= 8000);
    }

    @Test
    void boundedOutputKeepsHeadAndTailForVeryLongContentWithinBudget() {
        String content = "HEAD_SENTINEL" + "x".repeat(50_000) + "TAIL_SENTINEL";
        VueProjectBuilder.BoundedOutput output = boundedOutput(content);

        assertTrue(output.value().startsWith("HEAD_SENTINEL"));
        assertTrue(output.value().endsWith("TAIL_SENTINEL"));
        assertTrue(output.value().contains("...[truncated]..."));
        assertTrue(output.value().length() <= 8000);
    }

    @Test
    void processRunnerKillsCapturedDescendantHoldingPipesAfterRootExit() throws Exception {
        Path childReady = tempDir.resolve("child-ready");
        Path releaseRoot = tempDir.resolve("release-root");
        List<String> command = javaFixtureCommand("parent", childReady, releaseRoot);
        var runner = new VueProjectBuilder.ProcessCommandRunner();
        CompletableFuture<VueProjectBuilder.CommandResult> execution = CompletableFuture.supplyAsync(() -> {
            try {
                return runner.run(tempDir.toFile(), command, 10);
            } catch (Exception e) {
                throw new RuntimeException(e);
            }
        });

        long childPid = -1;
        try {
            awaitCondition(
                    () -> Files.exists(childReady) && !Files.readString(childReady).isBlank(),
                    Duration.ofSeconds(5));
            childPid = Long.parseLong(Files.readString(childReady));
            Files.writeString(releaseRoot, "release");

            VueProjectBuilder.CommandResult result = execution.get(8, TimeUnit.SECONDS);

            assertEquals(-1, result.exitCode());
            long capturedPid = childPid;
            awaitCondition(
                    () -> ProcessHandle.of(capturedPid).map(handle -> !handle.isAlive()).orElse(true),
                    Duration.ofSeconds(3));
        } finally {
            Files.writeString(releaseRoot, "release");
            try {
                execution.get(8, TimeUnit.SECONDS);
            } catch (Exception ignored) {
                execution.cancel(true);
            }
            if (childPid <= 0 && Files.exists(childReady)) {
                String persistedPid = Files.readString(childReady);
                if (!persistedPid.isBlank()) {
                    childPid = Long.parseLong(persistedPid);
                }
            }
            if (childPid > 0) {
                ProcessHandle.of(childPid).ifPresent(ProcessHandle::destroyForcibly);
            }
        }
    }

    private VueProjectBuilder.BoundedOutput boundedOutput(String content) {
        VueProjectBuilder.BoundedOutput output = new VueProjectBuilder.BoundedOutput();
        char[] chars = content.toCharArray();
        output.append(chars, 0, chars.length);
        return output;
    }

    private List<String> javaFixtureCommand(String mode, Path childReady, Path releaseRoot) {
        String executable = Path.of(
                System.getProperty("java.home"),
                "bin",
                System.getProperty("os.name").toLowerCase(Locale.ROOT).contains("windows")
                        ? "java.exe"
                        : "java").toString();
        return List.of(
                executable,
                "-cp",
                System.getProperty("java.class.path"),
                DescendantHoldingPipeFixture.class.getName(),
                mode,
                childReady.toString(),
                releaseRoot.toString());
    }

    private void awaitCondition(CheckedCondition condition, Duration timeout) throws Exception {
        assertTimeoutPreemptively(timeout, () -> {
            while (!condition.isSatisfied()) {
                Thread.sleep(10);
            }
        });
    }

    @FunctionalInterface
    private interface CheckedCondition {
        boolean isSatisfied() throws Exception;
    }

    public static final class DescendantHoldingPipeFixture {
        private DescendantHoldingPipeFixture() {
        }

        public static void main(String[] args) throws Exception {
            Path childReady = Path.of(args[1]);
            Path releaseRoot = Path.of(args[2]);
            if ("child".equals(args[0])) {
                Path stagedReady = childReady.resolveSibling(childReady.getFileName() + ".tmp");
                Files.writeString(stagedReady, Long.toString(ProcessHandle.current().pid()));
                Files.move(
                        stagedReady,
                        childReady,
                        StandardCopyOption.ATOMIC_MOVE,
                        StandardCopyOption.REPLACE_EXISTING);
                new CountDownLatch(1).await();
                return;
            }

            new ProcessBuilder(javaCommand("child", childReady, releaseRoot))
                    .inheritIO()
                    .start();
            while (!Files.exists(childReady)) {
                Thread.sleep(10);
            }
            System.out.println("CHILD_PID=" + Files.readString(childReady));
            System.out.println("READY");
            System.out.flush();
            while (!Files.exists(releaseRoot)) {
                Thread.sleep(10);
            }
        }

        private static List<String> javaCommand(String mode, Path childReady, Path releaseRoot) {
            String executable = Path.of(
                    System.getProperty("java.home"),
                    "bin",
                    System.getProperty("os.name").toLowerCase(Locale.ROOT).contains("windows")
                            ? "java.exe"
                            : "java").toString();
            return List.of(
                    executable,
                    "-cp",
                    System.getProperty("java.class.path"),
                    DescendantHoldingPipeFixture.class.getName(),
                    mode,
                    childReady.toString(),
                    releaseRoot.toString());
        }
    }
}
