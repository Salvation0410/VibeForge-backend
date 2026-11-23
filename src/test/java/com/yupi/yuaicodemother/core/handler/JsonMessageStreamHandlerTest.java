package com.yupi.yuaicodemother.core.handler;

import cn.hutool.json.JSONUtil;
import com.yupi.yuaicodemother.model.entity.ChatHistoryOriginal;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.service.ChatHistoryOriginalService;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import org.junit.jupiter.api.Test;
import org.springframework.test.util.ReflectionTestUtils;
import reactor.core.publisher.Flux;

import java.util.ArrayList;
import java.util.List;
import java.util.HashSet;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class JsonMessageStreamHandlerTest {
    @Test
    void pythonFileResultsShowPathWithoutNullOrRepeatedSource() {
        var handler = new JsonMessageStreamHandler();
        for (String name : new String[]{"file_read", "file_write"}) {
            String chunk = result(name, true);
            String output = ReflectionTestUtils.invokeMethod(handler, "handleJsonMessageChunk", chunk,
                    new StringBuilder(), new StringBuilder(), new ArrayList<>(), new HashSet<>());
            assertTrue(output.contains("src/App.vue"));
            assertFalse(output.contains("null"));
            assertFalse(output.contains("private-source"));
        }
    }

    @Test
    void toolRequestsKeepDeduplicatedProgressWithoutPollutingHistory() {
        var history = mock(ChatHistoryService.class);
        var original = mock(ChatHistoryOriginalService.class);
        String request = request("file_write", "call-1");
        var output = new JsonMessageStreamHandler().handle(Flux.just(
                request, request, request("project_build", "internal-1"),
                request("image_search", "image-1"),
                JSONUtil.toJsonStr(Map.of("type", "tool_request", "name", "file_write")),
                request("file_read", "read-1"), request("dir_read", "dir-1"),
                request("file_modify", "modify-1"), request("file_delete", "delete-1"),
                result("file_write", false)), history, original, 42, SysUser.builder().id(7L).build())
                .collectList().block();
        assertEquals(List.of("\n\n[选择工具] 写入文件\n\n", "\n\n[选择工具] 读取文件\n\n",
                "\n\n[选择工具] 读取目录\n\n", "\n\n[选择工具] 修改文件\n\n",
                "\n\n[选择工具] 删除文件\n\n", "\n\n[工具调用] 写入文件 src/App.vue（未完成）\n\n"), output);
        verify(history).addChatMessage(42L, output.getLast(), "ai", 7L);
        verify(original).addOriginalChatMessageBatch(argThat(records -> records.size() == 2));
        verify(original, never()).addOriginalChatMessage(anyLong(), anyString(), anyString(), anyLong());
    }

    @Test
    void internalToolsRemainInRawHistoryWithoutDisplayText() {
        List<ChatHistoryOriginal> records = new ArrayList<>();
        var text = new StringBuilder("之前的回答");
        String chunk = result("project_build", true);
        String output = ReflectionTestUtils.invokeMethod(new JsonMessageStreamHandler(), "handleJsonMessageChunk", chunk,
                new StringBuilder(), text, records, new HashSet<>());
        assertEquals("", output);
        assertEquals(2, records.size());
        assertEquals("之前的回答", JSONUtil.parseObj(records.getFirst().getMessage()).getStr("text"));
        assertEquals(chunk, records.getLast().getMessage());
        assertEquals(0, text.length());
    }

    @Test
    void completedStreamPersistsPairedToolRecordsAndTrailingText() {
        var history = mock(ChatHistoryService.class);
        var original = mock(ChatHistoryOriginalService.class);
        String chunk = result("file_write", true);
        var output = new JsonMessageStreamHandler().handle(Flux.just(
                JSONUtil.toJsonStr(Map.of("type", "ai_response", "data", "开始")), chunk,
                JSONUtil.toJsonStr(Map.of("type", "ai_response", "data", "结束"))),
                history, original, 42, SysUser.builder().id(7L).build()).collectList().block();
        assertEquals("开始", output.getFirst());
        assertEquals("结束", output.getLast());
        verify(history).addChatMessage(42L, String.join("", output), "ai", 7L);
        verify(original).addOriginalChatMessageBatch(argThat(records -> records.size() == 2
                && records.stream().allMatch(row -> row.getAppId() == 42L && row.getUserId() == 7L)
                && JSONUtil.parseObj(records.getFirst().getMessage()).getStr("text").equals("开始")
                && records.getLast().getMessage().equals(chunk)));
        verify(original).addOriginalChatMessage(42L, "结束", "ai", 7L);
    }

    private static String request(String name, String id) {
        return JSONUtil.toJsonStr(Map.of("type", "tool_request", "id", id, "name", name));
    }

    private static String result(String name, boolean ok) {
        return JSONUtil.toJsonStr(Map.of("type", "tool_executed", "engine", "langgraph", "id", "call-1",
                "name", name, "arguments", JSONUtil.toJsonStr(Map.of("relativeFilePath", "src/App.vue")),
                "result", JSONUtil.toJsonStr(Map.of("ok", ok, "content", "private-source"))));
    }
}
