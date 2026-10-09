package com.yupi.yuaicodemother.core.handler;

import cn.hutool.json.JSONUtil;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;

class JsonMessageStreamHandlerTest {
    @Test
    void legacyResultStringStillUsesOriginalFormatter() {
        var handler = new JsonMessageStreamHandler();
        var manager = org.mockito.Mockito.mock(com.yupi.yuaicodemother.ai.tools.ToolManager.class);
        org.mockito.Mockito.when(manager.getTool("writeFile"))
                .thenReturn(new com.yupi.yuaicodemother.ai.tools.FileWriteTool());
        ReflectionTestUtils.setField(handler, "toolManager", manager);
        String chunk = JSONUtil.toJsonStr(Map.of("type", "tool_executed", "id", "legacy-1", "name", "writeFile",
                "arguments", "{\"relativeFilePath\":\"src/App.vue\",\"content\":\"legacy-source\"}", "result", "文件写入成功"));
        String output = ReflectionTestUtils.invokeMethod(handler, "handleJsonMessageChunk", chunk,
                new StringBuilder(), new StringBuilder(), new ArrayList<>(), new HashSet<>());
        assertTrue(output.contains("legacy-source"));
        assertTrue(output.contains("src/App.vue"));
    }

    @Test
    void pythonFileResultsShowPathWithoutNullOrRepeatedSource() {
        var handler = new JsonMessageStreamHandler();
        for (String name : new String[]{"file_read", "file_write"}) {
            String chunk = JSONUtil.toJsonStr(Map.of("type", "tool_executed", "engine", "langgraph", "id", "call-1",
                    "name", name, "arguments", "{\"relativeFilePath\":\"src/App.vue\"}",
                    "result", "{\"ok\":true,\"content\":\"private-source\"}"));
            String output = ReflectionTestUtils.invokeMethod(handler, "handleJsonMessageChunk", chunk,
                    new StringBuilder(), new StringBuilder(), new ArrayList<>(), new HashSet<>());
            assertTrue(output.contains("src/App.vue"));
            assertFalse(output.contains("null"));
            assertFalse(output.contains("private-source"));
        }
    }
}
