package com.yupi.yuaicodemother.ai.gateway;

import com.mybatisflex.core.query.QueryWrapper;
import com.yupi.yuaicodemother.core.artifact.ArtifactPathResolver;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.mapper.AppMapper;
import com.yupi.yuaicodemother.model.entity.App;
import com.yupi.yuaicodemother.model.entity.ChatHistory;
import com.yupi.yuaicodemother.service.ChatHistoryService;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.*;

class GenerationInputContextProviderTest {
    @TempDir Path directory;

    @Test
    void historyIsBoundedOrderedAndCurrentMessageIsNotRepeated() {
        var mapper = mock(AppMapper.class);
        var history = mock(ChatHistoryService.class);
        var resolver = mock(ArtifactPathResolver.class);
        var app = new App();
        app.setUserId(7L);
        app.setInitPrompt("中".repeat(2001));
        when(mapper.selectOneById(42L)).thenReturn(app);
        when(resolver.resolveActiveRoot(CodeGenTypeEnum.HTML, 42L)).thenReturn(directory);
        var current = new ChatHistory();
        current.setMessage("优化一下");
        var previous = new ChatHistory();
        previous.setMessage("中".repeat(999) + "😀");
        var initial = new ChatHistory();
        initial.setMessage("咖啡店网站");
        when(history.list(any(QueryWrapper.class))).thenReturn(List.of(current, previous, initial));
        var context = new GenerationInputContextProvider(mapper, history, resolver)
                .build(42L, 7L, "优化一下", CodeGenTypeEnum.HTML);
        var conversation = (List<?>) context.get("conversation");
        assertEquals(2, conversation.size());
        assertEquals("咖啡店网站", ((Map<?, ?>) conversation.getFirst()).get("content"));
        assertEquals("中".repeat(999), ((Map<?, ?>) conversation.getLast()).get("content"));
        var metadata = (Map<?, ?>) context.get("metadata");
        assertEquals(true, metadata.get("existingProject"));
        assertEquals(true, metadata.get("historyTruncated"));
        assertEquals(true, metadata.get("initialPromptTruncated"));
        assertEquals(2000, metadata.get("initialPrompt").toString().length());
    }

    @Test
    void anotherOwnersHistoryIsNeverRead() {
        var mapper = mock(AppMapper.class);
        var history = mock(ChatHistoryService.class);
        var resolver = mock(ArtifactPathResolver.class);
        var app = new App();
        app.setUserId(8L);
        when(mapper.selectOneById(42L)).thenReturn(app);
        assertThrows(BusinessException.class, () -> new GenerationInputContextProvider(mapper, history, resolver)
                .build(42L, 7L, "优化一下", CodeGenTypeEnum.HTML));
        verifyNoInteractions(history, resolver);
    }
}
