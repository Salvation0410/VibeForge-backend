package com.yupi.yuaicodemother.langraph4j.demo;

import org.bsc.langgraph4j.state.AgentState;
import org.bsc.langgraph4j.state.Channels;
import org.bsc.langgraph4j.state.Channel;

import java.util.*;

// 定义状态


class SimpleState extends AgentState {
    public static final String MESSAGES_KEY = "messages";

    // 定义状态结构。
    // MESSAGES_KEY 保存字符串列表，新消息会追加到列表末尾。

    //创建一个schema 通过appender方法增加数据 [1,] [a,2]
    public static final Map<String, Channel<?>> SCHEMA = Map.of(
            MESSAGES_KEY, Channels.appender(ArrayList::new)
    );

    public SimpleState(Map<String, Object> initData) {
        super(initData);
    }

    public List<String> messages() {
        return this.<List<String>>value("messages")
                .orElse( List.of() );
    }
}
