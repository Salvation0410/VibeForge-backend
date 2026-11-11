package com.yupi.yuaicodemother.core.builder;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class VueComponentContractCheckTest {
    @TempDir Path directory;
    private static final String OK = "{\"version\":1,\"status\":\"ok\",\"checkedComponents\":2,\"checkedUsages\":1,\"skippedFiles\":0,\"skippedUsages\":0,\"truncated\":false,\"issues\":[]}";

    @Test
    void parsesBoundedSuccessAndFailure() {
        assertTrue(VueComponentContractCheck.parse(OK).built());
        String failed = OK.replace("\"ok\"", "\"failed\"").replace("[]", "[{\"file\":\"src/App.vue\",\"component\":\"Child\",\"prop\":\"value\"}]");
        var result = VueComponentContractCheck.parse(failed);
        assertFalse(result.built());
        assertEquals("VUE_COMPONENT_CONTRACT_FAILED", result.errorCode());
        assertTrue(result.message().contains("Child.value"));
        assertEquals("VUE_COMPONENT_CHECK_UNAVAILABLE", VueComponentContractCheck.parse(failed.replace("src/App.vue", "../App.vue")).errorCode());
    }

    @ParameterizedTest
    @ValueSource(strings = {"", "not-json", "{}", "{\"version\":1,\"status\":\"ok\",\"issues\":[]}", "null"})
    void malformedProtocolFailsClosedWithoutRawMessage(String text) {
        var result = VueComponentContractCheck.parse(text);
        assertFalse(result.built());
        assertEquals("VUE_COMPONENT_CHECK_UNAVAILABLE", result.errorCode());
        assertFalse(result.message().contains("not-json"));
    }

    @Test
    void rejectsContradictionDuplicatesAndInvalidCounts() {
        for (String text : List.of(OK + " {}", OK.replace("\"ok\"", "\"failed\""),
                OK.replace("\"version\":1", "\"version\":1,\"version\":1"),
                OK.replace("\"version\":1", "\"version\":4294967297"),
                OK.replace("\"checkedUsages\":1", "\"checkedUsages\":-1"))) {
            assertEquals("VUE_COMPONENT_CHECK_UNAVAILABLE", VueComponentContractCheck.parse(text).errorCode());
        }
    }

    @Test
    void removesTemporaryScriptAndSuppressesExecutionErrors() throws Exception {
        Path[] created = new Path[1];
        var result = VueComponentContractCheck.verify(directory.toFile(), (cwd, command, timeout) -> {
            assertEquals("node", command.getFirst());
            assertEquals(15, timeout);
            created[0] = Path.of(command.get(1));
            assertTrue(Files.isRegularFile(created[0]));
            return new VueProjectBuilder.CommandResult(1, "private-source", "private-error");
        });
        assertFalse(Files.exists(created[0]));
        assertEquals("VUE_COMPONENT_CHECK_UNAVAILABLE", result.errorCode());
        assertFalse(result.message().contains("private"));
    }

    @Test
    void interruptionPropagatesAndRemovesTemporaryScript() {
        Path[] created = new Path[1];
        assertThrows(InterruptedException.class, () -> VueComponentContractCheck.verify(directory.toFile(), (cwd, command, timeout) -> {
            created[0] = Path.of(command.get(1));
            throw new InterruptedException();
        }));
        assertFalse(Files.exists(created[0]));
    }

    @Test
    @EnabledIfEnvironmentVariable(named = "VUE_COMPONENT_INTEGRATION", matches = "true")
    void realNodeAndInstalledCompilerRejectThenAcceptHandwrittenComponents() throws Exception {
        Path compilerRoot = Path.of(System.getenv("VUE_CHECK_COMPILER_ROOT")).toRealPath();
        Path modules = directory.resolve("node_modules");
        var runner = new VueProjectBuilder.ProcessCommandRunner();
        Files.writeString(directory.resolve("package.json"), "{}");
        Files.writeString(directory.resolve("Child.vue"), "<script setup>defineProps({value:{required:true}})</script><template><div/></template>");
        Path parent = directory.resolve("App.vue");
        Files.writeString(parent, "<script setup>import Child from './Child.vue'</script><template><Child/></template>");
        try {
            // 仅在本测试的临时目录创建依赖链接；不安装依赖，不接触生成工程。
            var link = runner.run(directory.toFile(), List.of("node", "-e",
                    "require('fs').symlinkSync(process.argv[1],process.argv[2],'junction')",
                    compilerRoot.resolve("node_modules").toString(), modules.toString()), 5);
            assertEquals(0, link.exitCode());
            assertEquals("VUE_COMPONENT_CONTRACT_FAILED", VueComponentContractCheck.verify(directory.toFile(), runner).errorCode());
            Files.writeString(parent, "<script setup>import Child from './Child.vue'</script><template><Child :value=\"1\"/></template>");
            assertTrue(VueComponentContractCheck.verify(directory.toFile(), runner).built());
        } finally {
            // 先移除链接本身，防止临时目录的自动递归清理涉及外部依赖。
            Files.deleteIfExists(modules);
        }
    }
}
