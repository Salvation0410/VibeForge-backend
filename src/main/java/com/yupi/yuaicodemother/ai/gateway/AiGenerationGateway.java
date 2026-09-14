package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import reactor.core.publisher.Flux;

/** AI 代码生成边界，屏蔽本地模型和独立 LangGraph 服务的差异。 */
public interface AiGenerationGateway {
    CodeGenTypeEnum route(String prompt, Long appId, Long userId, String requestId);

    Flux<String> generate(String prompt, CodeGenTypeEnum codeGenType, Long appId, Long userId, String requestId);
}
