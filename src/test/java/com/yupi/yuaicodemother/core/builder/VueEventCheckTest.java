package com.yupi.yuaicodemother.core.builder;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class VueEventCheckTest {
    static final String OK = "{\"version\":3,\"status\":\"ok\",\"checkedUsages\":1,\"skippedUsages\":0,\"truncated\":false,\"reason\":\"NONE\",\"issues\":[],\"advisories\":[],\"payloadChecked\":0,\"payloadSkipped\":0,\"payloadAdvisories\":[],\"argumentChecked\":0,\"argumentSkipped\":0,\"argumentAdvisories\":[]}";
    private static final String EVIDENCE = "[{\"file\":\"src/App.vue\",\"component\":\"Card\",\"event\":\"add\"}]";
    @TempDir Path directory;

    @Test
    @EnabledIfEnvironmentVariable(named = "VUE_COMPONENT_INTEGRATION", matches = "true")
    void realNodeProjectsLiteralAdvisoryThroughStrictJavaProtocol() throws Exception {
        Path compilerRoot = Path.of(System.getenv("VUE_CHECK_COMPILER_ROOT")).toRealPath();
        Path modules = directory.resolve("node_modules");
        var runner = new VueProjectBuilder.ProcessCommandRunner();
        Files.createDirectories(directory.resolve("src"));
        Files.writeString(directory.resolve("src/Hero.vue"), "<script setup>const emit=defineEmits(['shop-now'])</script><template><button @click=\"emit('shop-now','featured')\"/><button @click=\"emit('shop-now','new')\"/></template>");
        Path parent = directory.resolve("src/App.vue");
        Files.writeString(parent, "<script setup>import Hero from './Hero.vue';function go(){}</script><template><Hero @shop-now=\"go\"/></template>");
        try {
            // 仅链接可信解析器依赖，不运行组件；最终移除链接本身以保护外部依赖。
            var link = runner.run(directory.toFile(), List.of("node", "-e",
                    "require('fs').symlinkSync(process.argv[1],process.argv[2],'junction')",
                    compilerRoot.resolve("node_modules").toString(), modules.toString()), 5);
            assertEquals(0, link.exitCode());
            var result = VueComponentContractCheck.verifyEvents(directory.toFile(), runner);
            assertTrue(result.built());
            assertTrue(result.message().contains("IGNORED_LITERAL_VARIANTS src/App.vue -> Hero @shop-now"));
            assertTrue(result.message().contains("不能仅凭线索判错"));
            assertFalse(result.message().contains("featured"));
            Files.writeString(parent, "<script setup>import Hero from './Hero.vue';function go(target){use(target)}</script><template><Hero @shop-now=\"go\"/></template>");
            var consumed = VueComponentContractCheck.verifyEvents(directory.toFile(), runner);
            assertTrue(consumed.built());
            assertFalse(consumed.message().contains("IGNORED_LITERAL_VARIANTS"));
        } finally {
            Files.deleteIfExists(modules);
        }
    }

    @Test
    void parsesStrictFailureAndNonBlockingAdvisories() {
        assertTrue(VueEventCheck.parse(OK).built());
        String failed = OK.replace("\"status\":\"ok\"", "\"status\":\"failed\"")
                .replace("\"issues\":[]", "\"issues\":" + EVIDENCE);
        assertEquals("VUE_EVENT_CONTRACT_FAILED", VueEventCheck.parse(failed).errorCode());
        var advisory = VueEventCheck.parse(OK.replace("\"advisories\":[]", "\"advisories\":" + EVIDENCE));
        assertTrue(advisory.built());
        assertTrue(advisory.message().contains("仅供审查"));
        assertTrue(advisory.message().contains("Card @add"));
        assertTrue(VueEventCheck.parse(OK.replace("\"reason\":\"NONE\"", "\"reason\":\"INCOMPLETE\"")
                .replace("\"checkedUsages\":1", "\"checkedUsages\":0")
                .replace("\"truncated\":false", "\"truncated\":true")).message().contains("已跳过"));
    }

    @Test
    void rejectsMalformedOrContradictoryProtocolWithoutEchoingInput() {
        for (String bad : List.of("private-source", "{}", OK + " {}",
                OK.replace("\"version\":3", "\"version\":3,\"version\":3"),
                OK.replace("\"checkedUsages\":1", "\"checkedUsages\":-1"),
                OK.replace("\"status\":\"ok\"", "\"status\":\"failed\""),
                OK.replace("\"issues\":[]", "\"issues\":" + EVIDENCE),
                OK.replace("\"advisories\":[]", "\"advisories\":" + EVIDENCE.replace("src/App.vue", "../App.vue")),
                OK.replace("\"truncated\":false", "\"truncated\":true"))) {
            var result = VueEventCheck.parse(bad);
            assertEquals("VUE_EVENT_CHECK_UNAVAILABLE", result.errorCode());
            assertFalse(result.message().contains("private-source"));
        }
    }

    @Test
    void payloadAdvisoryRemainsSuccessfulAndProjectsOnlyFixedEvidence() {
        var result = VueEventCheck.parse(payload());
        assertTrue(result.built());
        assertTrue(result.message().contains("MISSING_DESTRUCTURED_KEYS src/App.vue -> ProductSection @add-to-cart 缺键=product"));
        assertTrue(result.message().contains("不能仅凭线索判错"));
        // 名称硬门禁仍跳过 v-for；有限载荷遍历可独立得到一项可信审查线索。
        var loop = VueEventCheck.parse(payload().replace("\"checkedUsages\":1", "\"checkedUsages\":0")
                .replace("\"skippedUsages\":0", "\"skippedUsages\":1"));
        assertTrue(loop.built());
        assertTrue(loop.message().contains("缺键=product"));
        assertTrue(VueEventCheck.parse(OK.replace("\"checkedUsages\":1", "\"checkedUsages\":0")
                .replace("\"payloadSkipped\":0", "\"payloadSkipped\":1")).built());
    }

    @Test
    void rejectsOldVersionUnknownKindsAndUnboundedPayloadEvidence() {
        for (String bad : List.of(
                OK.replace("\"version\":3", "\"version\":1"),
                payload().replace("MISSING_DESTRUCTURED_KEYS", "ARBITRARY_REASON"),
                payload().replace("\"product\"", "\"private-source\",\"extra\",\"third\",\"fourth\",\"fifth\""),
                payload().replace("[\"product\"]", "[]"),
                payload().replace("[\"product\"]", "[\"product\",\"product\"]"),
                payload().replace("[\"product\"]", "[123]"),
                payload().replace("[\"product\"]", "[\"product.id\"]"),
                payload().replace("src/App.vue", "../App.vue"),
                payload().replace("\"payloadChecked\":1", "\"payloadChecked\":0"),
                payload().replace("\"payloadSkipped\":0", "\"payloadSkipped\":20001"),
                payload().replace("\"reason\":\"NONE\"", "\"reason\":\"INCOMPLETE\"")
                        .replace("\"checkedUsages\":1", "\"checkedUsages\":0").replace("\"truncated\":false", "\"truncated\":true"),
                payload().replace("\"missingKeys\"", "\"unknown\""),
                payload().replace("\"missingKeys\":[\"product\"]", "\"missingKeys\":[\"product\"],\"source\":\"private-source\""))) {
            var result = VueEventCheck.parse(bad);
            assertEquals("VUE_EVENT_CHECK_UNAVAILABLE", result.errorCode());
            assertFalse(result.message().contains("private-source"));
        }
    }

    private static String payload() {
        return OK.replace("\"payloadChecked\":0", "\"payloadChecked\":1")
                .replace("\"payloadAdvisories\":[]", "\"payloadAdvisories\":[{\"kind\":\"MISSING_DESTRUCTURED_KEYS\",\"file\":\"src/App.vue\",\"component\":\"ProductSection\",\"event\":\"add-to-cart\",\"missingKeys\":[\"product\"]}]");
    }

    private static String arguments() {
        return OK.replace("\"argumentChecked\":0", "\"argumentChecked\":1")
                .replace("\"argumentAdvisories\":[]", "\"argumentAdvisories\":[{\"kind\":\"IGNORED_LITERAL_VARIANTS\",\"file\":\"src/App.vue\",\"component\":\"HeroSection\",\"event\":\"shop-now\"}]");
    }

    @Test
    void argumentAdvisoryOnlyPromptsReviewAndKeepsPayloadEvidence() {
        var result = VueEventCheck.parse(arguments());
        assertTrue(result.built());
        assertTrue(result.message().contains("IGNORED_LITERAL_VARIANTS src/App.vue -> HeroSection @shop-now"));
        assertTrue(result.message().contains("零形参监听可能丢失目标或状态"));
        assertTrue(result.message().contains("合法忽略"));
        assertTrue(result.message().contains("不能仅凭线索判错"));
        assertTrue(VueEventCheck.parse(arguments().replace("\"checkedUsages\":1", "\"checkedUsages\":0")).built());
        assertTrue(VueEventCheck.parse(OK.replace("\"argumentChecked\":0", "\"argumentChecked\":20000")
                .replace("\"argumentSkipped\":0", "\"argumentSkipped\":20000")).built());
        var both = VueEventCheck.parse(payload().replace("\"argumentChecked\":0", "\"argumentChecked\":1")
                .replace("\"argumentAdvisories\":[]", "\"argumentAdvisories\":[{\"kind\":\"IGNORED_LITERAL_VARIANTS\",\"file\":\"src/App.vue\",\"component\":\"HeroSection\",\"event\":\"shop-now\"}]"));
        assertTrue(both.built());
        assertTrue(both.message().contains("缺键=product"));
        assertTrue(both.message().contains("IGNORED_LITERAL_VARIANTS"));
    }

    @Test
    void rejectsArgumentProtocolTamperingWithoutLeakingArbitraryInput() {
        String evidence = "{\"kind\":\"IGNORED_LITERAL_VARIANTS\",\"file\":\"src/App.vue\",\"component\":\"HeroSection\",\"event\":\"shop-now\"}";
        for (String bad : List.of(
                OK.replace("\"version\":3", "\"version\":2"),
                OK.replace("\"argumentChecked\":0,", ""),
                OK.replace("\"argumentChecked\":0", "\"unknown\":0"),
                OK.replace("\"argumentAdvisories\":[]", "\"argumentAdvisories\":{}"),
                arguments().replace("IGNORED_LITERAL_VARIANTS", "private-source"),
                arguments().replace("\"kind\":\"IGNORED_LITERAL_VARIANTS\"", "\"kind\":123"),
                arguments().replace("\"kind\":\"IGNORED_LITERAL_VARIANTS\"", "\"kind\":\"IGNORED_LITERAL_VARIANTS\",\"source\":\"private-source\""),
                arguments().replace("\"event\":\"shop-now\"", "\"literal\":\"private-source\""),
                arguments().replace("\"argumentChecked\":1", "\"argumentChecked\":0"),
                arguments().replace("\"argumentChecked\":1", "\"argumentChecked\":-1"),
                arguments().replace("\"argumentChecked\":1", "\"argumentChecked\":1.0"),
                arguments().replace("\"argumentChecked\":1", "\"argumentChecked\":\"1\""),
                arguments().replace("\"argumentChecked\":1", "\"argumentChecked\":20001"),
                arguments().replace("\"argumentSkipped\":0", "\"argumentSkipped\":true"),
                arguments().replace("\"argumentSkipped\":0", "\"argumentSkipped\":-1"),
                arguments().replace("\"argumentSkipped\":0", "\"argumentSkipped\":20001"),
                arguments().replace("src/App.vue", "../App.vue"),
                arguments().replace("src/App.vue", "/App.vue"),
                arguments().replace("src/App.vue", "C:/App.vue"),
                arguments().replace("\"file\":\"src/App.vue\"", "\"file\":123"),
                arguments().replace("\"component\":\"HeroSection\"", "\"component\":\"private-source()\""),
                arguments().replace("\"event\":\"shop-now\"", "\"event\":\"private-source()\""),
                arguments().replace("[" + evidence + "]", "[" + String.join(",", java.util.Collections.nCopies(5, evidence)) + "]"),
                incomplete().replace("\"argumentChecked\":0", "\"argumentChecked\":1"),
                incomplete().replace("\"argumentSkipped\":0", "\"argumentSkipped\":1"),
                incomplete().replace("\"argumentAdvisories\":[]", "\"argumentAdvisories\":[" + evidence + "]"))) {
            var result = VueEventCheck.parse(bad);
            assertEquals("VUE_EVENT_CHECK_UNAVAILABLE", result.errorCode(), bad);
            assertFalse(result.message().contains("private-source"));
        }
    }

    private static String incomplete() {
        return OK.replace("\"reason\":\"NONE\"", "\"reason\":\"INCOMPLETE\"")
                .replace("\"checkedUsages\":1", "\"checkedUsages\":0")
                .replace("\"truncated\":false", "\"truncated\":true");
    }

    @Test
    void buildRunsPropsEventsAnchorsAndKeepsAllSummaries() throws Exception {
        Files.writeString(directory.resolve("package.json"), "{}");
        Files.createDirectory(directory.resolve("dist"));
        Files.writeString(directory.resolve("dist/index.html"), "<html/>");
        List<String> checks = new ArrayList<>();
        var builder = new VueProjectBuilder((cwd, command, timeout) -> {
            if (!command.getFirst().equals("node")) return new VueProjectBuilder.CommandResult(0, "ok", "");
            String mode = command.contains("--events") ? "events" : command.contains("--anchors") ? "anchors" : "props";
            checks.add(mode);
            return new VueProjectBuilder.CommandResult(0, switch (mode) {
                case "events" -> payload().replace("\"argumentChecked\":0", "\"argumentChecked\":1")
                        .replace("\"argumentAdvisories\":[]", "\"argumentAdvisories\":[{\"kind\":\"IGNORED_LITERAL_VARIANTS\",\"file\":\"src/App.vue\",\"component\":\"HeroSection\",\"event\":\"shop-now\"}]");
                case "anchors" -> VueAnchorCheckTest.OK;
                default -> "{\"version\":1,\"status\":\"ok\",\"checkedComponents\":1,\"checkedUsages\":1,\"skippedFiles\":0,\"skippedUsages\":0,\"truncated\":false,\"issues\":[]}";
            }, "");
        });
        var result = builder.buildProjectDetailed(directory.toString());
        assertTrue(result.built());
        assertEquals(List.of("props", "events", "anchors"), checks);
        assertTrue(result.message().contains("必填属性"));
        assertTrue(result.message().contains("事件"));
        assertTrue(result.message().contains("锚点"));
        assertTrue(result.message().contains("缺键=product"));
        assertTrue(result.message().contains("IGNORED_LITERAL_VARIANTS"));
    }

    @Test
    void eventRunnerUsesTrustedScriptAndCleansUpOnInterruption() throws Exception {
        Path[] script = new Path[1];
        assertThrows(InterruptedException.class, () -> VueComponentContractCheck.verifyEvents(directory.toFile(), (cwd, command, timeout) -> {
            assertTrue(command.contains("--events"));
            script[0] = Path.of(command.get(1));
            throw new InterruptedException();
        }));
        assertFalse(Files.exists(script[0]));
    }

    @Test
    void eventFailureStopsBeforeAnchorCheck() throws Exception {
        Files.writeString(directory.resolve("package.json"), "{}");
        Files.createDirectory(directory.resolve("dist"));
        Files.writeString(directory.resolve("dist/index.html"), "<html/>");
        String props = "{\"version\":1,\"status\":\"ok\",\"checkedComponents\":1,\"checkedUsages\":1,\"skippedFiles\":0,\"skippedUsages\":0,\"truncated\":false,\"issues\":[]}";
        for (String output : List.of("{}", OK.replace("\"status\":\"ok\"", "\"status\":\"failed\"")
                .replace("\"issues\":[]", "\"issues\":" + EVIDENCE))) {
            List<String> checks = new ArrayList<>();
            var builder = new VueProjectBuilder((cwd, command, timeout) -> {
                if (!command.getFirst().equals("node")) return new VueProjectBuilder.CommandResult(0, "ok", "");
                String mode = command.contains("--events") ? "events" : command.contains("--anchors") ? "anchors" : "props";
                checks.add(mode);
                return new VueProjectBuilder.CommandResult(0, mode.equals("props") ? props : output, "");
            });
            var result = builder.buildProjectDetailed(directory.toString());
            assertFalse(result.built());
            assertEquals(output.equals("{}") ? "VUE_EVENT_CHECK_UNAVAILABLE" : "VUE_EVENT_CONTRACT_FAILED", result.errorCode());
            assertEquals(List.of("props", "events"), checks);
        }
    }
}
