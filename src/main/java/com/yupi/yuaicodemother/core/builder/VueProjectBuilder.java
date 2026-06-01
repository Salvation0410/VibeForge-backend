package com.yupi.yuaicodemother.core.builder;

import cn.hutool.core.io.IoUtil;
import cn.hutool.core.util.RuntimeUtil;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.TimeUnit;

@Component
public class VueProjectBuilder {

    private static final Logger log = LoggerFactory.getLogger(VueProjectBuilder.class);

    private final ConcurrentHashMap<String, CompletableFuture<Boolean>> buildTaskMap = new ConcurrentHashMap<>();

    public void buildProjectAsync(String projectPath) {
        String normalizedProjectPath = normalizeProjectPath(projectPath);
        CompletableFuture<Boolean> buildFuture = new CompletableFuture<>();
        CompletableFuture<Boolean> existingFuture = buildTaskMap.putIfAbsent(normalizedProjectPath, buildFuture);
        if (existingFuture != null) {
            log.info("Vue 项目已存在进行中的构建任务，跳过重复异步构建: {}", normalizedProjectPath);
            return;
        }

        Thread.ofVirtual().name("vue-builder-" + System.currentTimeMillis()).start(() -> {
            try {
                boolean buildResult = doBuildProject(normalizedProjectPath);
                buildFuture.complete(buildResult);
            } catch (Exception e) {
                buildFuture.completeExceptionally(e);
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
        String normalizedProjectPath = normalizeProjectPath(projectPath);
        File projectDir = new File(normalizedProjectPath);
        if (!isVueProject(projectDir)) {
            return false;
        }
        if (hasReadyDist(projectDir)) {
            return true;
        }

        CompletableFuture<Boolean> existingFuture = buildTaskMap.get(normalizedProjectPath);
        if (existingFuture != null) {
            log.info("等待进行中的 Vue 构建任务完成: {}", normalizedProjectPath);
            return waitForBuild(existingFuture, normalizedProjectPath);
        }

        return buildProject(normalizedProjectPath);
    }

    public boolean buildProject(String projectPath) {
        String normalizedProjectPath = normalizeProjectPath(projectPath);
        CompletableFuture<Boolean> buildFuture = new CompletableFuture<>();
        CompletableFuture<Boolean> existingFuture = buildTaskMap.putIfAbsent(normalizedProjectPath, buildFuture);
        if (existingFuture != null) {
            log.info("检测到 Vue 项目正在构建，复用当前构建结果: {}", normalizedProjectPath);
            return waitForBuild(existingFuture, normalizedProjectPath);
        }

        try {
            boolean buildResult = doBuildProject(normalizedProjectPath);
            buildFuture.complete(buildResult);
            return buildResult;
        } catch (Exception e) {
            buildFuture.completeExceptionally(e);
            log.error("同步构建 Vue 项目时发生异常: {}", e.getMessage(), e);
            return false;
        } finally {
            buildTaskMap.remove(normalizedProjectPath, buildFuture);
        }
    }

    private boolean doBuildProject(String projectPath) {
        File projectDir = new File(projectPath);
        if (!projectDir.isDirectory()) {
            log.error("项目目录不存在: {}", projectPath);
            return false;
        }

        File packageJson = new File(projectDir, "package.json");
        if (!packageJson.isFile()) {
            log.error("package.json 文件不存在: {}", packageJson.getAbsolutePath());
            return false;
        }

        log.info("开始构建 Vue 项目: {}", projectPath);
        if (!executeNpmInstall(projectDir)) {
            log.error("npm install 执行失败");
            return false;
        }
        if (!executeNpmBuild(projectDir)) {
            log.error("npm run build 执行失败");
            return false;
        }

        File distDir = getDistDirectory(projectDir);
        File distIndexFile = new File(distDir, "index.html");
        if (!distDir.isDirectory() || !distIndexFile.isFile()) {
            log.error("构建完成但 dist/index.html 未生成: {}", distIndexFile.getAbsolutePath());
            return false;
        }
        log.info("Vue 项目构建成功，dist 目录: {}", distDir.getAbsolutePath());
        return true;
    }

    private boolean waitForBuild(CompletableFuture<Boolean> buildFuture, String projectPath) {
        try {
            return buildFuture.get(360, TimeUnit.SECONDS);
        } catch (Exception e) {
            log.error("等待 Vue 项目构建结果失败: {}", projectPath, e);
            return false;
        }
    }

    private boolean executeNpmInstall(File projectDir) {
        log.info("执行 npm install...");
        String command = String.format("%s install", buildCommand("npm"));
        return executeCommand(projectDir, command, 300);
    }

    private boolean executeNpmBuild(File projectDir) {
        log.info("执行 npm run build...");
        String command = String.format("%s run build", buildCommand("npm"));
        return executeCommand(projectDir, command, 180);
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

    private boolean executeCommand(File workingDir, String command, int timeoutSeconds) {
        try {
            log.info("在目录 {} 中执行命令: {}", workingDir.getAbsolutePath(), command);
            Process process = RuntimeUtil.exec(
                    null,
                    workingDir,
                    command.split("\\s+")
            );

            boolean finished = process.waitFor(timeoutSeconds, TimeUnit.SECONDS);
            if (!finished) {
                log.error("命令执行超时（{} 秒），强制终止进程", timeoutSeconds);
                process.destroyForcibly();
                return false;
            }

            String stdout = IoUtil.read(process.getInputStream(), StandardCharsets.UTF_8);
            String stderr = IoUtil.read(process.getErrorStream(), StandardCharsets.UTF_8);
            int exitCode = process.exitValue();
            if (!stdout.isBlank()) {
                log.info("命令标准输出:\n{}", stdout);
            }
            if (!stderr.isBlank()) {
                log.warn("命令错误输出:\n{}", stderr);
            }

            if (exitCode == 0) {
                log.info("命令执行成功: {}", command);
                return true;
            }

            log.error("命令执行失败，退出码: {}", exitCode);
            return false;
        } catch (Exception e) {
            log.error("执行命令失败: {}, 错误信息: {}", command, e.getMessage(), e);
            return false;
        }
    }

    private String normalizeProjectPath(String projectPath) {
        try {
            return new File(projectPath).getCanonicalPath();
        } catch (Exception e) {
            return new File(projectPath).getAbsolutePath();
        }
    }
}
