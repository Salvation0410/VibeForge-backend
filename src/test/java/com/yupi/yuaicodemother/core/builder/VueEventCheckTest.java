package com.yupi.yuaicodemother.core.builder;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.*;

class VueEventCheckTest {
    static final String OK = "{\"version\":1,\"status\":\"ok\",\"checkedUsages\":1,\"skippedUsages\":0,\"truncated\":false,\"reason\":\"NONE\",\"issues\":[],\"advisories\":[]}";
    private static final String EVIDENCE = "[{\"file\":\"src/App.vue\",\"component\":\"Card\",\"event\":\"add\"}]";
    @TempDir Path directory;

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
                OK.replace("\"version\":1", "\"version\":1,\"version\":1"),
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
                case "events" -> OK;
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
