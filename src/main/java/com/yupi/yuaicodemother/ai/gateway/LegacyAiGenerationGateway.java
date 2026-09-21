package com.yupi.yuaicodemother.ai.gateway;

import com.yupi.yuaicodemother.ai.AiCodeGenTypeRoutingService;
import com.yupi.yuaicodemother.ai.AiCodeGenTypeRoutingServiceFactory;
import com.yupi.yuaicodemother.core.AiCodeGeneratorFacade;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import reactor.core.publisher.Flux;

/**
 * 现有 LangChain4j 生成链路的网关适配器，用于迁移期间回退和灰度对照。
 */
@Component
@RequiredArgsConstructor
public class LegacyAiGenerationGateway implements AiGenerationGateway {
    private final AiCodeGeneratorFacade facade;
    private final AiCodeGenTypeRoutingServiceFactory routingFactory;

    /**
     * 使用既有路由服务判断代码生成类型。
     *
     * @param prompt 用户输入的应用生成需求
     * @param appId 应用 ID；Legacy 路由当前不使用该参数
     * @param userId 当前用户 ID；Legacy 路由当前不使用该参数
     * @param requestId 请求唯一标识；Legacy 路由当前不使用该参数
     * @return 既有 LangChain4j 路由服务选择的生成类型
     */
    @Override
    public CodeGenTypeEnum route(String prompt, Long appId, Long userId, String requestId) {
        AiCodeGenTypeRoutingService service = routingFactory.createAiCodeGenTypeRoutingService();
        return service.routeCodeGenType(prompt);
    }

    /**
     * 使用既有代码生成门面执行流式生成和文件保存。
     *
     * @param prompt 用户输入的代码生成或修改需求
     * @param codeGenType 应用代码生成类型
     * @param appId 应用 ID
     * @param userId 当前用户 ID；Legacy 门面当前不使用该参数
     * @param requestId 请求唯一标识；Legacy 门面当前不使用该参数
     * @return 既有生成链路输出的字符串数据流
     */
    @Override
    public Flux<String> generate(String prompt, CodeGenTypeEnum codeGenType, Long appId, Long userId, String requestId) {
        return facade.generateAndSaveCodeStream(prompt, codeGenType, appId, requestId);
    }

    /** 标记 Legacy 请求已取消，使无法中断的模型流在回调完成时拒绝构建或发布。 */
    @Override
    public void cancel(Long appId, Long userId, String requestId) {
        facade.cancelGeneration(requestId);
    }
}
