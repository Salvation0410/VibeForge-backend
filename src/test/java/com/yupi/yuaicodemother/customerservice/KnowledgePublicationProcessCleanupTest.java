package com.yupi.yuaicodemother.customerservice;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;

import java.nio.file.*;
import java.time.Duration;
import java.util.*;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;
import static org.junit.jupiter.api.Assumptions.assumeTrue;

/** 不连接任何服务，单独验证Windows虚拟环境启动器及其后代的超时退出。 */
class KnowledgePublicationProcessCleanupTest {
    @Test void timeoutTerminatesOnlyOwnedLauncherAndItsDescendants() throws Exception {
        assumeTrue(Files.isRegularFile(KnowledgePublicationOwnedProcess.pythonPath()),"本地Python虚拟环境不存在，跳过独立进程探针");
        Path python=KnowledgePublicationOwnedProcess.python();
        Path parent=Path.of("tmp");Files.createDirectories(parent);
        Path directory=Files.createTempDirectory(parent,"rag-publication-process-probe-").toAbsolutePath();
        Path marker=directory.resolve("pids.json");
        String script="import json,os,pathlib,subprocess,sys,time; "
                +"child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)']); "
                +"pathlib.Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]),encoding='utf-8'); time.sleep(120)";
        try(var process=KnowledgePublicationOwnedProcess.start(new ProcessBuilder(python.toString(),"-X","utf8","-c",script,marker.toString())
                .redirectError(directory.resolve("stderr.log").toFile()).redirectOutput(directory.resolve("stdout.log").toFile()))) {
            long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(15);
            while(!Files.exists(marker) && System.nanoTime()<deadline) assertFalse(process.await(Duration.ofMillis(50)));
            assertTrue(Files.exists(marker),"实际解释器及子进程必须到达探针");
            List<Long> children=new ObjectMapper().readValue(Files.readString(marker),
                    new com.fasterxml.jackson.core.type.TypeReference<>() { });
            assertEquals(2,children.size());assertTrue(children.stream().allMatch(pid->ProcessHandle.of(pid).orElseThrow().isAlive()));
            assertFalse(process.await(Duration.ofMillis(200)),"探针必须真正经过超时分支");
            Set<Long> owned=process.pids();assertTrue(owned.containsAll(children),"等待期间须捕获启动器下的真实解释器和后代");
            process.terminateAndConfirm();
            assertTrue(owned.stream().allMatch(pid->ProcessHandle.of(pid).map(handle->!handle.isAlive()).orElse(true)));
            new ObjectMapper().writeValue(directory.resolve("result.json").toFile(),Map.of("observedProcesses",owned.size(),"allExited",true));
        }
    }
}

/** 仅管理单次ProcessBuilder创建的进程及实际观察到的后代，禁止按进程名全局终止。 */
final class KnowledgePublicationOwnedProcess implements AutoCloseable {
    private final Process root;
    private final Map<Long,ProcessHandle> observed=new LinkedHashMap<>();

    private KnowledgePublicationOwnedProcess(Process root) {this.root=root;observe();}

    static Path python() {
        Path python=pythonPath();
        assertTrue(Files.isRegularFile(python),"先在ai-service执行uv sync --frozen --python 3.12");return python;
    }

    static Path pythonPath() {
        return Path.of("ai-service",".venv",System.getProperty("os.name").startsWith("Windows")?"Scripts/python.exe":"bin/python").toAbsolutePath();
    }

    static KnowledgePublicationOwnedProcess start(ProcessBuilder builder) throws Exception {
        return new KnowledgePublicationOwnedProcess(builder.start());
    }

    private void observe() {
        observed.putIfAbsent(root.pid(),root.toHandle());
        // 即使启动器已退出，已观察到的后代仍须继续追踪，不能在finally丢失其句柄。
        for(ProcessHandle handle:List.copyOf(observed.values()))
            if(handle.isAlive()) handle.descendants().forEach(child->observed.putIfAbsent(child.pid(),child));
    }

    boolean await(Duration timeout) throws InterruptedException {
        long deadline=System.nanoTime()+timeout.toNanos();
        do {
            observe();
            if(!root.isAlive()) return true;
            long remaining=deadline-System.nanoTime();
            if(remaining<=0) return false;
            root.waitFor(Math.min(remaining,TimeUnit.MILLISECONDS.toNanos(50)),TimeUnit.NANOSECONDS);
        } while(true);
    }

    int exitValue() {return root.exitValue();}
    Set<Long> pids() {observe();return Set.copyOf(observed.keySet());}

    void terminateAndConfirm() throws Exception {
        observe();
        if(root.isAlive() && System.getProperty("os.name").startsWith("Windows")) {
            // /T只作用于本例仍存活的精确根PID；保存的句柄用于覆盖启动器退出的竞态。
            Process terminator=new ProcessBuilder("taskkill","/PID",Long.toString(root.pid()),"/T","/F")
                    .redirectOutput(ProcessBuilder.Redirect.DISCARD).redirectError(ProcessBuilder.Redirect.DISCARD).start();
            if(!terminator.waitFor(5,TimeUnit.SECONDS)) {
                terminator.destroyForcibly();assertTrue(terminator.waitFor(5,TimeUnit.SECONDS),"本例taskkill必须有界退出");
            }
        }
        long deadline=System.nanoTime()+TimeUnit.SECONDS.toNanos(10);
        do {
            observe();
            var handles=new ArrayList<>(observed.values());Collections.reverse(handles);
            for(ProcessHandle handle:handles) if(handle.isAlive()) handle.destroyForcibly();
            if(observed.values().stream().noneMatch(ProcessHandle::isAlive)) return;
            Thread.sleep(25);
        } while(System.nanoTime()<deadline);
        fail("本例进程树未全部退出，禁止清理仍可能被迟到写入的Milvus资源");
    }

    @Override public void close() throws Exception {terminateAndConfirm();}
}
