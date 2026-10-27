package com.yupi.yuaicodemother.ai.gateway;

/**
 * 仅用于网关与静态流处理器之间的内部帧，不进入公共 SSE 或聊天历史。
 * 保留前缀区分展示增量与成功候选，避免沿用 Flux 字符串接口时把草稿拼入最终回复。
 */
public final class StaticGenerationStreamFrame {
    private static final String DISPLAY_PREFIX = "\u0000langgraph-static:display:";
    private static final String CANDIDATE_PREFIX = "\u0000langgraph-static:candidate:";

    private StaticGenerationStreamFrame() { }

    public static String display(String content) {
        return DISPLAY_PREFIX + content;
    }

    public static String candidate(String content) {
        return CANDIDATE_PREFIX + content;
    }

    public static boolean isDisplay(String value) {
        return value.startsWith(DISPLAY_PREFIX);
    }

    public static boolean isCandidate(String value) {
        return value.startsWith(CANDIDATE_PREFIX);
    }

    public static String content(String value) {
        if (isDisplay(value)) return value.substring(DISPLAY_PREFIX.length());
        if (isCandidate(value)) return value.substring(CANDIDATE_PREFIX.length());
        throw new IllegalArgumentException("未知静态流内部帧");
    }
}
