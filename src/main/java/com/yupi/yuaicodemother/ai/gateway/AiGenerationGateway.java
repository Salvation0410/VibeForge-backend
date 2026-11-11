package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.model.dto.app.StructuredEditIntent;
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

    /** 旧引擎必须明确拒绝尚不支持的编辑协议，不能静默丢弃结构化意图。 */
    default Flux<String> generate(String prompt, CodeGenTypeEnum codeGenType, Long appId, Long userId,
                                  String requestId, StructuredEditIntent editIntent) {
        if (editIntent != null) {
            return Flux.error(new GenerationStreamException(ErrorCode.PARAMS_ERROR.getCode(),
                    "STRUCTURED_EDIT_UNSUPPORTED", requestId, "当前生成引擎不支持可视化属性修改，请切换到 LangGraph 引擎", null));
        }
        return generate(prompt, codeGenType, appId, userId, requestId);
    }

    /**
     * 请求取消正在运行的生成；不支持主动中断的引擎至少应阻止后续发布。
     *
     * @param appId 应用 ID
     * @param userId 当前用户 ID
     * @param requestId 要取消的生成请求 ID
     */
    default void cancel(Long appId, Long userId, String requestId) {
        // 默认实现兼容尚不具备取消能力的网关。
    }
}
