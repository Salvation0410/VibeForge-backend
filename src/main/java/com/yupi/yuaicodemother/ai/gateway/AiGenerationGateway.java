package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import reactor.core.publisher.Flux;

/**
 * AI 代码生成统一边界，屏蔽本地 LangChain4j 与独立 LangGraph 服务的实现差异。
 */
public interface AiGenerationGateway {
    /**
     * 根据用户初始提示词确定代码生成类型。
     *
     * @param prompt 用户输入的应用生成需求
     * @param appId 应用 ID；创建应用尚未分配 ID 时可以为空
     * @param userId 当前用户 ID，用于灰度选择和链路追踪
     * @param requestId 本次路由请求的唯一标识
     * @return AI 判断出的代码生成类型
     */
    CodeGenTypeEnum route(String prompt, Long appId, Long userId, String requestId);

    /**
     * 按指定生成类型执行流式代码生成。
     *
     * @param prompt 用户输入的代码生成或修改需求
     * @param codeGenType 应用当前采用的代码生成类型
     * @param appId 应用 ID
     * @param userId 当前用户 ID，用于灰度选择和请求元数据
     * @param requestId 本次生成请求的唯一标识
     * @return 可被既有 SSE 处理链消费的字符串数据流
     */
    Flux<String> generate(String prompt, CodeGenTypeEnum codeGenType, Long appId, Long userId, String requestId);
}
