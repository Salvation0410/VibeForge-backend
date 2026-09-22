package com.yupi.yuaicodemother.core.builder;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletionException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

@Component
public class VueProjectBuilder {

    private static final Logger log = LoggerFactory.getLogger(VueProjectBuilder.class);

    private static final Pattern THOUSANDS_SEPARATED_PROPERTY_NUMBER = Pattern.compile(
            "(:\\s*)([0-9]{1,3}(?:,[0-9]{3})+(?:\\.[0-9]+)?)(\\s*[,}\\]])"
    );

    private static final int MAX_MESSAGE_LENGTH = 4000;
    private static final int MAX_COMMAND_OUTPUT_LENGTH = 8000;
    private static final int BUILD_WAIT_TIMEOUT_SECONDS = 540;
    private static final int PROCESS_TERMINATION_TIMEOUT_SECONDS = 2;
    private static final int OUTPUT_READ_TIMEOUT_SECONDS = 2;
    private static final String TRUNCATION_MARKER = System.lineSeparator() + "...[truncated]..." + System.lineSeparator();

    private final ConcurrentHashMap<String, CompletableFuture<VueBuildResult>> buildTaskMap = new ConcurrentHashMap<>();
    private final CommandRunner commandRunner;

    public VueProjectBuilder() {
        this(new ProcessCommandRunner());
    }

    VueProjectBuilder(CommandRunner commandRunner) {
        this.commandRunner = commandRunner;
    }

    public void buildProjectAsync(String projectPath) {
        String normalizedProjectPath = normalizeProjectPath(projectPath);
        CompletableFuture<VueBuildResult> buildFuture = new CompletableFuture<>();
        CompletableFuture<VueBuildResult> existingFuture = buildTaskMap.putIfAbsent(normalizedProjectPath, buildFuture);
        if (existingFuture != null) {
            log.info("Vue 项目已存在进行中的构建任务，跳过重复异步构建: {}", normalizedProjectPath);
            return;
        }

        Thread.ofVirtual().name("vue-builder-" + System.currentTimeMillis()).start(() -> {
            try {
                VueBuildResult buildResult = doBuildProject(normalizedProjectPath);
                buildFuture.complete(buildResult);
            } catch (Exception e) {
                buildFuture.complete(unexpectedFailure(normalizedProjectPath, e));
                log.error("异步构建 Vue 项目时发生异常: {}", e.getMessage(), e);
            } finally {
                buildTaskMap.remove(normalizedProjectPath, buildFuture);
            }
        });
    }

    public boolean isVueProject(File projectDir) {
        if (projectDir == null || !projectDir.isDirectory()) {
            return false;
        }
        return new File(projectDir, "package.json").isFile();
    }

    public File getDistDirectory(File projectDir) {
        return new File(projectDir, "dist");
    }

    public boolean hasReadyDist(File projectDir) {
        File distDir = getDistDirectory(projectDir);
        return distDir.isDirectory() && new File(distDir, "index.html").isFile();
    }

    public boolean ensureProjectBuilt(String projectPath) {
        return ensureProjectBuiltDetailed(projectPath).built();
    }

    public VueBuildResult ensureProjectBuiltDetailed(String projectPath) {
        String normalizedProjectPath = normalizeProjectPath(projectPath);
        File projectDir = new File(normalizedProjectPath);
        if (!projectDir.isDirectory()) {
            return VueBuildResult.failure("VUE_PROJECT_DIR_MISSING", "project directory is missing");
        }
        if (!new File(projectDir, "package.json").isFile()) {
            return VueBuildResult.failure("VUE_PACKAGE_JSON_MISSING", "package.json is missing");
        }
        if (hasReadyDist(projectDir)) {
            return VueBuildResult.success();
        }

        CompletableFuture<VueBuildResult> existingFuture = buildTaskMap.get(normalizedProjectPath);
        if (existingFuture != null) {
            log.info("等待进行中的 Vue 构建任务完成: {}", normalizedProjectPath);
            return waitForBuild(existingFuture, normalizedProjectPath);
        }

        return buildProjectDetailed(normalizedProjectPath);
    }

    public boolean buildProject(String projectPath) {
        return buildProjectDetailed(projectPath).built();
    }

    public VueBuildResult buildProjectDetailed(String projectPath) {
        String normalizedProjectPath = normalizeProjectPath(projectPath);
        while (true) {
            CompletableFuture<VueBuildResult> buildFuture = new CompletableFuture<>();
            CompletableFuture<VueBuildResult> existingFuture = buildTaskMap.putIfAbsent(
                    normalizedProjectPath, buildFuture);
            if (existingFuture != null) {
                log.info("检测到 Vue 项目正在构建，等待后执行本次强制构建: {}", normalizedProjectPath);
                VueBuildResult waitFailure = waitForPriorBuild(existingFuture, normalizedProjectPath);
                if (waitFailure != null) {
                    return waitFailure;
                }
                buildTaskMap.remove(normalizedProjectPath, existingFuture);
                continue;
            }

            try {
                VueBuildResult buildResult = doBuildProject(normalizedProjectPath);
                buildFuture.complete(buildResult);
                return buildResult;
            } catch (Exception e) {
                VueBuildResult failure = unexpectedFailure(normalizedProjectPath, e);
                buildFuture.complete(failure);
                log.error("同步构建 Vue 项目时发生异常: {}", e.getMessage(), e);
                return failure;
            } finally {
                buildTaskMap.remove(normalizedProjectPath, buildFuture);
            }
        }
    }

    private VueBuildResult doBuildProject(String projectPath) {
        File projectDir = new File(projectPath);
        if (!projectDir.isDirectory()) {
            log.error("项目目录不存在: {}", projectPath);
            return VueBuildResult.failure("VUE_PROJECT_DIR_MISSING", "project directory is missing");
        }

        File packageJson = new File(projectDir, "package.json");
        if (!packageJson.isFile()) {
            log.error("package.json 文件不存在: {}", packageJson.getAbsolutePath());
            return VueBuildResult.failure("VUE_PACKAGE_JSON_MISSING", "package.json is missing");
        }

        log.info("开始构建 Vue 项目: {}", projectPath);
        normalizeGeneratedSourceFiles(projectDir);
        CommandResult installResult = executeNpmInstall(projectDir);
        if (!installResult.succeeded()) {
            log.error("npm install 执行失败");
            return commandFailure("VUE_NPM_INSTALL_FAILED", "npm install", installResult, projectPath);
        }
        CommandResult buildResult = executeNpmBuild(projectDir);
        if (!buildResult.succeeded()) {
            log.error("npm run build 执行失败");
            return commandFailure("VUE_NPM_BUILD_FAILED", "npm run build", buildResult, projectPath);
        }

        File distDir = getDistDirectory(projectDir);
        File distIndexFile = new File(distDir, "index.html");
        if (!distDir.isDirectory() || !distIndexFile.isFile()) {
            log.error("构建完成但 dist/index.html 未生成: {}", distIndexFile.getAbsolutePath());
            return VueBuildResult.failure("VUE_DIST_INDEX_MISSING", "dist/index.html was not generated");
        }
        log.info("Vue 项目构建成功，dist 目录: {}", distDir.getAbsolutePath());
        return VueBuildResult.success();
    }

    private VueBuildResult waitForBuild(CompletableFuture<VueBuildResult> buildFuture, String projectPath) {
        try {
            return buildFuture.get(BUILD_WAIT_TIMEOUT_SECONDS, TimeUnit.SECONDS);
        } catch (Exception e) {
            log.error("等待 Vue 项目构建结果失败: {}", projectPath, e);
            return VueBuildResult.failure("VUE_BUILD_WAIT_FAILED",
                    sanitizeMessage("waiting for the Vue build failed: " + safeExceptionMessage(e), projectPath));
        }
    }

    private VueBuildResult waitForPriorBuild(
            CompletableFuture<VueBuildResult> buildFuture,
            String projectPath) {
        try {
            buildFuture.get(BUILD_WAIT_TIMEOUT_SECONDS, TimeUnit.SECONDS);
            return null;
        } catch (Exception e) {
            log.error("等待已有 Vue 构建任务结束失败: {}", projectPath, e);
            return VueBuildResult.failure("VUE_BUILD_WAIT_FAILED",
                    sanitizeMessage("waiting for the previous Vue build failed: "
                            + safeExceptionMessage(e), projectPath));
        }
    }

    private CommandResult executeNpmInstall(File projectDir) {
        log.info("执行 npm install...");
        return executeCommand(projectDir, List.of(buildCommand("npm"), "install"), 300);
    }

    private CommandResult executeNpmBuild(File projectDir) {
        log.info("执行 npm run build...");
        return executeCommand(projectDir, List.of(buildCommand("npm"), "run", "build"), 180);
    }

    private void normalizeGeneratedSourceFiles(File projectDir) {
        File srcDir = new File(projectDir, "src");
        if (!srcDir.isDirectory()) {
            return;
        }

        try (var paths = Files.walk(srcDir.toPath())) {
            paths.filter(Files::isRegularFile)
                    .filter(this::isGeneratedSourceFile)
                    .forEach(this::normalizeThousandsSeparatedPropertyNumbers);
        } catch (IOException e) {
            log.warn("Normalize generated source files failed: {}", srcDir.getAbsolutePath(), e);
        }
    }

    private boolean isGeneratedSourceFile(Path path) {
        String fileName = path.getFileName().toString().toLowerCase();
        return fileName.endsWith(".vue")
                || fileName.endsWith(".js")
                || fileName.endsWith(".mjs")
                || fileName.endsWith(".ts")
                || fileName.endsWith(".jsx")
                || fileName.endsWith(".tsx");
    }

    private void normalizeThousandsSeparatedPropertyNumbers(Path path) {
        try {
            String content = Files.readString(path, StandardCharsets.UTF_8);
            Matcher matcher = THOUSANDS_SEPARATED_PROPERTY_NUMBER.matcher(content);
            String normalized = matcher.replaceAll(matchResult ->
                    matchResult.group(1)
                            + matchResult.group(2).replace(",", "")
                            + matchResult.group(3)
            );
            if (!normalized.equals(content)) {
                Files.writeString(path, normalized, StandardCharsets.UTF_8);
                log.info("Normalized thousands-separated numeric literals in generated source: {}", path.toAbsolutePath());
            }
        } catch (IOException e) {
            log.warn("Normalize generated source file failed: {}", path.toAbsolutePath(), e);
        }
    }

    private boolean isWindows() {
        return System.getProperty("os.name").toLowerCase().contains("windows");
    }

    private String buildCommand(String baseCommand) {
        if (isWindows()) {
            return baseCommand + ".cmd";
        }
        return baseCommand;
    }

    private CommandResult executeCommand(File workingDir, List<String> command, int timeoutSeconds) {
        try {
            log.info("在目录 {} 中执行命令: {}", workingDir.getAbsolutePath(), command);
            CommandResult result = commandRunner.run(workingDir, command, timeoutSeconds);
            String stdout = result.stdout();
            String stderr = result.stderr();
            if (!stdout.isBlank()) {
                log.info("命令标准输出:\n{}", sanitizeMessage(stdout, workingDir.getPath()));
            }
            if (!stderr.isBlank()) {
                log.warn("命令错误输出:\n{}", sanitizeMessage(stderr, workingDir.getPath()));
            }

            if (result.succeeded()) {
                log.info("命令执行成功: {}", command);
                return result;
            }

            log.error("命令执行失败，退出码: {}", result.exitCode());
            return result;
        } catch (Exception e) {
            log.error("执行命令失败: {}, 错误信息: {}", command, e.getMessage(), e);
            return new CommandResult(-1, "", safeExceptionMessage(e));
        }
    }

    private VueBuildResult commandFailure(String code, String operation, CommandResult result, String projectPath) {
        String details = result.stderr().isBlank() ? result.stdout() : result.stderr();
        String message = operation + " failed (exit code " + result.exitCode() + ")";
        if (!details.isBlank()) {
            message += ": " + details;
        }
        return VueBuildResult.failure(code, sanitizeMessage(message, projectPath));
    }

    private VueBuildResult unexpectedFailure(String projectPath, Exception exception) {
        return VueBuildResult.failure("VUE_BUILD_FAILED",
                sanitizeMessage("Vue build failed: " + safeExceptionMessage(exception), projectPath));
    }

    private String sanitizeMessage(String message, String projectPath) {
        String sanitized = message == null ? "" : message;
        List<String> projectPathVariants = new ArrayList<>();
        for (String path : List.of(projectPath, new File(projectPath).getAbsolutePath())) {
            projectPathVariants.add(path);
            projectPathVariants.add(path.replace('\\', '/'));
            projectPathVariants.add(path.replace('/', '\\'));
        }
        int pathFlags = isWindows() ? Pattern.CASE_INSENSITIVE | Pattern.UNICODE_CASE : 0;
        for (String path : projectPathVariants.stream().distinct().toList()) {
            if (path != null && !path.isBlank()) {
                sanitized = Pattern.compile(Pattern.quote(path), pathFlags)
                        .matcher(sanitized)
                        .replaceAll(Matcher.quoteReplacement("[redacted]"));
            }
        }

        List<String> secrets = new ArrayList<>();
        System.getenv().values().stream()
                .filter(value -> value != null && value.length() >= 4)
                .sorted(Comparator.comparingInt(String::length).reversed())
                .forEach(secrets::add);
        for (String secret : secrets) {
            if (secret != null && !secret.isBlank()) {
                sanitized = sanitized.replace(secret, "[redacted]");
            }
        }
        return keepHeadAndTail(sanitized, MAX_MESSAGE_LENGTH);
    }

    private static String keepHeadAndTail(String value, int maxLength) {
        if (value.length() <= maxLength) {
            return value;
        }
        int contentLength = maxLength - TRUNCATION_MARKER.length();
        int headLength = contentLength / 2;
        int tailLength = contentLength - headLength;
        return value.substring(0, headLength)
                + TRUNCATION_MARKER
                + value.substring(value.length() - tailLength);
    }

    private static String safeExceptionMessage(Throwable throwable) {
        Throwable cause = throwable instanceof CompletionException && throwable.getCause() != null
                ? throwable.getCause()
                : throwable;
        String message = cause.getMessage();
        return message == null || message.isBlank() ? cause.getClass().getSimpleName() : message;
    }

    private String normalizeProjectPath(String projectPath) {
        try {
            return new File(projectPath).getCanonicalPath();
        } catch (Exception e) {
            return new File(projectPath).getAbsolutePath();
        }
    }

    @FunctionalInterface
    interface CommandRunner {
        CommandResult run(File workingDirectory, List<String> command, int timeoutSeconds) throws Exception;
    }

    record CommandResult(int exitCode, String stdout, String stderr) {
        CommandResult {
            stdout = stdout == null ? "" : stdout;
            stderr = stderr == null ? "" : stderr;
        }

        boolean succeeded() {
            return exitCode == 0;
        }
    }

    record OutputCapture(String output, boolean timedOut) {
    }

    static final class BoundedOutput {
        private static final int CONTENT_LIMIT = MAX_COMMAND_OUTPUT_LENGTH - TRUNCATION_MARKER.length();
        private static final int HEAD_LIMIT = CONTENT_LIMIT / 2;
        private static final int TAIL_LIMIT = CONTENT_LIMIT - HEAD_LIMIT;
        private final StringBuilder complete = new StringBuilder(MAX_COMMAND_OUTPUT_LENGTH);
        private final StringBuilder head = new StringBuilder(HEAD_LIMIT);
        private final StringBuilder tail = new StringBuilder(TAIL_LIMIT);
        private boolean truncated;

        void append(char[] value, int offset, int length) {
            if (!truncated && complete.length() + length <= MAX_COMMAND_OUTPUT_LENGTH) {
                complete.append(value, offset, length);
                return;
            }

            if (!truncated) {
                truncated = true;
                int fromComplete = Math.min(HEAD_LIMIT, complete.length());
                head.append(complete, 0, fromComplete);
                int fromValue = Math.min(HEAD_LIMIT - fromComplete, length);
                if (fromValue > 0) {
                    head.append(value, offset, fromValue);
                }

                if (length >= TAIL_LIMIT) {
                    tail.append(value, offset + length - TAIL_LIMIT, TAIL_LIMIT);
                } else {
                    int fromCompleteTail = Math.min(TAIL_LIMIT - length, complete.length());
                    tail.append(complete, complete.length() - fromCompleteTail, complete.length());
                    tail.append(value, offset, length);
                }
                complete.setLength(0);
                return;
            }

            tail.append(value, offset, length);
            if (tail.length() > TAIL_LIMIT) {
                tail.delete(0, tail.length() - TAIL_LIMIT);
            }
        }

        String value() {
            return truncated ? head + TRUNCATION_MARKER + tail : complete.toString();
        }
    }

    static OutputCapture awaitOutput(
            CompletableFuture<String> outputFuture,
            long timeout,
            TimeUnit timeUnit) {
        try {
            return new OutputCapture(outputFuture.get(timeout, timeUnit), false);
        } catch (TimeoutException e) {
            outputFuture.cancel(true);
            return new OutputCapture("", true);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            outputFuture.cancel(true);
            return new OutputCapture("", true);
        } catch (ExecutionException e) {
            outputFuture.cancel(true);
            return new OutputCapture("failed to read command output: " + safeExceptionMessage(e), true);
        }
    }

    static final class ProcessCommandRunner implements CommandRunner {
        @Override
        public CommandResult run(File workingDirectory, List<String> command, int timeoutSeconds) throws Exception {
            Process process = new ProcessBuilder(command)
                    .directory(workingDirectory)
                    .start();
            ExecutorService readers = Executors.newThreadPerTaskExecutor(
                    Thread.ofVirtual().name("vue-build-output-", 0).factory());
            CompletableFuture<String> stdout = CompletableFuture.supplyAsync(
                    () -> readOutput(process.getInputStream()), readers);
            CompletableFuture<String> stderr = CompletableFuture.supplyAsync(
                    () -> readOutput(process.getErrorStream()), readers);
            Set<ProcessHandle> capturedDescendants = new HashSet<>();
            try {
                boolean finished = waitForProcess(process, timeoutSeconds, capturedDescendants);
                if (!finished) {
                    terminateProcessTree(process, capturedDescendants);
                }
                OutputCapture stdoutCapture = awaitOutput(
                        stdout, OUTPUT_READ_TIMEOUT_SECONDS, TimeUnit.SECONDS);
                OutputCapture stderrCapture = awaitOutput(
                        stderr, OUTPUT_READ_TIMEOUT_SECONDS, TimeUnit.SECONDS);
                boolean incompleteOutput = stdoutCapture.timedOut() || stderrCapture.timedOut();
                int exitCode = !finished || process.isAlive() || incompleteOutput
                        ? -1
                        : process.exitValue();
                String error = stderrCapture.output();
                if (!finished) {
                    error = append(error, "command timed out");
                }
                if (incompleteOutput) {
                    error = append(error, "command output collection timed out or failed");
                }
                return new CommandResult(exitCode, stdoutCapture.output(), error);
            } finally {
                terminateProcessTree(process, capturedDescendants);
                closeQuietly(process.getInputStream());
                closeQuietly(process.getErrorStream());
                closeQuietly(process.getOutputStream());
                stdout.cancel(true);
                stderr.cancel(true);
                readers.shutdownNow();
            }
        }

        private static String readOutput(InputStream stream) {
            BoundedOutput output = new BoundedOutput();
            try (var reader = new InputStreamReader(stream, StandardCharsets.UTF_8)) {
                char[] buffer = new char[2048];
                int read;
                while ((read = reader.read(buffer)) >= 0) {
                    output.append(buffer, 0, read);
                }
                return output.value();
            } catch (IOException e) {
                throw new CompletionException(e);
            }
        }

        private static boolean waitForProcess(
                Process process,
                int timeoutSeconds,
                Set<ProcessHandle> capturedDescendants) throws InterruptedException {
            long deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(timeoutSeconds);
            while (process.isAlive()) {
                captureDescendants(process, capturedDescendants);
                long remainingNanos = deadline - System.nanoTime();
                if (remainingNanos <= 0) {
                    return false;
                }
                long waitMillis = Math.max(1, Math.min(
                        100,
                        TimeUnit.NANOSECONDS.toMillis(remainingNanos)));
                if (process.waitFor(waitMillis, TimeUnit.MILLISECONDS)) {
                    captureDescendants(process, capturedDescendants);
                    return true;
                }
            }
            captureDescendants(process, capturedDescendants);
            return true;
        }

        private static void captureDescendants(Process process, Set<ProcessHandle> capturedDescendants) {
            try {
                process.descendants().forEach(capturedDescendants::add);
            } catch (RuntimeException ignored) {
                // The root may exit while its descendants are being enumerated.
            }
        }

        private static void terminateProcessTree(
                Process process,
                Set<ProcessHandle> capturedDescendants) {
            captureDescendants(process, capturedDescendants);
            Set<ProcessHandle> targets = new HashSet<>(capturedDescendants);
            targets.add(process.toHandle());
            targets.stream().filter(ProcessHandle::isAlive).forEach(ProcessHandle::destroy);
            awaitHandles(targets);
            targets.stream().filter(ProcessHandle::isAlive).forEach(ProcessHandle::destroyForcibly);
            awaitHandles(targets);
        }

        private static void awaitHandles(Set<ProcessHandle> handles) {
            long deadline = System.nanoTime()
                    + TimeUnit.SECONDS.toNanos(PROCESS_TERMINATION_TIMEOUT_SECONDS);
            for (ProcessHandle handle : handles) {
                if (!handle.isAlive()) {
                    continue;
                }
                long remainingNanos = deadline - System.nanoTime();
                if (remainingNanos <= 0) {
                    return;
                }
                try {
                    handle.onExit().get(remainingNanos, TimeUnit.NANOSECONDS);
                } catch (InterruptedException e) {
                    Thread.currentThread().interrupt();
                    return;
                } catch (ExecutionException | TimeoutException ignored) {
                    return;
                }
            }
        }

        private static void closeQuietly(java.io.Closeable closeable) {
            try {
                closeable.close();
            } catch (IOException ignored) {
                // Best-effort cleanup after a bounded process wait.
            }
        }

        private static String append(String current, String suffix) {
            return current.isBlank() ? suffix : current + System.lineSeparator() + suffix;
        }

    }
}
