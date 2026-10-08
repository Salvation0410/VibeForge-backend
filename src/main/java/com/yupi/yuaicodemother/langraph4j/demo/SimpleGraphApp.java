package com.yupi.yuaicodemother.langraph4j.demo;

import org.bsc.langgraph4j.StateGraph;
import org.bsc.langgraph4j.GraphStateException;
import static org.bsc.langgraph4j.action.AsyncNodeAction.node_async;
import static org.bsc.langgraph4j.StateGraph.START;
import static org.bsc.langgraph4j.StateGraph.END;

import java.util.Map;

public class SimpleGraphApp {

    public static void main(String[] args) throws GraphStateException {
        // 初始化节点。
        GreeterNode greeterNode = new GreeterNode();
        ResponderNode responderNode = new ResponderNode();

        // 定义图结构。

        //定义 graph 结构
       var stateGraph = new StateGraph<>(SimpleState.SCHEMA, initData -> new SimpleState(initData))
            .addNode("greeter", node_async(greeterNode))
            .addNode("responder", node_async(responderNode))
            // 定义边，从问候节点开始。
            .addEdge(START, "greeter") // 从问候节点开始
            .addEdge("greeter", "responder")
            .addEdge("responder", END)   // 响应节点执行后结束
             ;
        // 编译图。
        //每次都系要先编译一手
        var compiledGraph = stateGraph.compile();

        // 运行图。
        // `stream` 方法返回 AsyncGenerator。
        // 示例直接收集结果；真实应用可以在结果到达时逐项处理。
        // 这里关注执行结束后的最终状态。

        for (var item : compiledGraph.stream( Map.of( SimpleState.MESSAGES_KEY, "Let's, begin!" ) ) ) {

            System.out.println( item );
        }

    }
}
