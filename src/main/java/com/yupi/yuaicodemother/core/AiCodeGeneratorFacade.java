package com.yupi.yuaicodemother.core;

import cn.hutool.json.JSONUtil;
import com.yupi.yuaicodemother.ai.AiCodeGeneratorService;
import com.yupi.yuaicodemother.ai.AiCodeGeneratorServiceFactory;
import com.yupi.yuaicodemother.ai.model.HtmlCodeResult;
import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;
import com.yupi.yuaicodemother.ai.model.message.AiResponseMessage;
import com.yupi.yuaicodemother.ai.model.message.ToolExecutedMessage;
import com.yupi.yuaicodemother.ai.model.message.ToolRequestMessage;
import com.yupi.yuaicodemother.constant.AppConstant;
import com.yupi.yuaicodemother.core.builder.VueProjectBuilder;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublicationService;
import dev.langchain4j.model.output.FinishReason;
import com.yupi.yuaicodemother.core.paser.CodeParserExecutor;
import com.yupi.yuaicodemother.core.saver.CodeFileSaverExecutor;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import dev.langchain4j.model.chat.response.ChatResponse;
import dev.langchain4j.service.TokenStream;
import dev.langchain4j.service.tool.ToolExecution;
import jakarta.annotation.Resource;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import reactor.core.publisher.Flux;

import java.io.File;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;

/**
 * @author huang
 * @version 1.0
 * @description 调用ai 整合文件写入
 * @date 2026/5/26
 */
@Service
@Slf4j
public class AiCodeGeneratorFacade {

    private final Set<String> cancelledRequests = ConcurrentHashMap.newKeySet();
    @Resource
    private AiCodeGeneratorServiceFactory aiCodeGeneratorServiceFactory;

    @Resource
    private VueProjectBuilder vueProjectBuilder;
    @Resource
    private ArtifactPublicationService artifactPublicationService;

    /**
     * 统一入口：根据类型生成并保存代码
     *
     * @param userMessage     用户提示词
     * @param codeGenTypeEnum 生成类型
     * @return 保存的目录
     */

    @Deprecated
    public File generateAndSaveCode(String userMessage, CodeGenTypeEnum codeGenTypeEnum,Long appId) {
        if (codeGenTypeEnum == null) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "生成类型为空");
        }
        //根据AppId + 生成代码应用的类型 获取相应的App AI Service服务实例
        AiCodeGeneratorService aiCodeGeneratorService = aiCodeGeneratorServiceFactory.getAiCodeGeneratorService(appId,codeGenTypeEnum);
        return switch (codeGenTypeEnum) {
            case HTML -> {
                HtmlCodeResult result = aiCodeGeneratorService.generateHtmlCode(userMessage);
                // yield作用：跳转到指定代码块，返回指定结果
                yield CodeFileSaverExecutor.executeSaver(result, CodeGenTypeEnum.HTML,appId);
            }
            case MULTI_FILE -> {
                MultiFileCodeResult result = aiCodeGeneratorService.generateMultiFileCode(userMessage);
                yield CodeFileSaverExecutor.executeSaver(result, CodeGenTypeEnum.MULTI_FILE,appId);
            }
            default -> {
                String errorMessage = "不支持的生成类型：" + codeGenTypeEnum.getValue();
                throw new BusinessException(ErrorCode.SYSTEM_ERROR, errorMessage);
            }
        };
    }

    /**
     * 统一入口：根据类型生成并保存代码（流式）
     *
     * @param userMessage     用户提示词
     * @param codeGenTypeEnum 生成类型
     * @param appId 应用Id
     * @return 流式响应
     */
    public Flux<String> generateAndSaveCodeStream(String userMessage, CodeGenTypeEnum codeGenTypeEnum,Long appId) {
        return generateAndSaveCodeStream(userMessage, codeGenTypeEnum, appId, java.util.UUID.randomUUID().toString());
    }

    /**
     * 按生成类型启动流式响应，并在模型正常结束后发布完整产物。
     *
     * @param userMessage 用户提示词
     * @param codeGenTypeEnum 生成类型
     * @param appId 应用 ID
     * @param requestId 当前请求及产物版本 ID
     * @return 仅在产物发布成功后正常完成的文本流
     */
    public Flux<String> generateAndSaveCodeStream(String userMessage, CodeGenTypeEnum codeGenTypeEnum, Long appId,
                                                   String requestId) {
        if (codeGenTypeEnum == null) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "生成类型为空");
        }
        //根据AppId获取相应的App AI Service服务实例
        AiCodeGeneratorService aiCodeGeneratorService = aiCodeGeneratorServiceFactory.getAiCodeGeneratorService(appId,codeGenTypeEnum);
        return switch (codeGenTypeEnum) {
            case HTML -> {
                TokenStream codeStream = aiCodeGeneratorService.generateHtmlCodeStream(userMessage);
                yield processSimpleTokenStream(codeStream, CodeGenTypeEnum.HTML, appId, requestId);
            }
            case MULTI_FILE -> {
                TokenStream codeStream = aiCodeGeneratorService.generateMultiFileCodeStream(userMessage);
                yield processSimpleTokenStream(codeStream, CodeGenTypeEnum.MULTI_FILE, appId, requestId);
            }
            case VUE_PROJECT -> {
               TokenStream tokenStream = aiCodeGeneratorService.generateVueProjectCodeStream(appId, userMessage);
                yield processTokenStream(tokenStream, appId, requestId);
            }
            default -> {
                String errorMessage = "不支持的生成类型：" + codeGenTypeEnum.getValue();
                throw new BusinessException(ErrorCode.SYSTEM_ERROR, errorMessage);
            }
        };
    }

    /**
     * 将 TokenStream 转换为 Flux<String>，并传递工具调用信息
     *
     * 这里使用了适配器的思想 不用单独去创建一个类返回
     *
     * @param tokenStream TokenStream 对象
     * @return Flux<String> 流式响应
     */
    private Flux<String> processTokenStream(TokenStream tokenStream, Long appId, String requestId) {
        return Flux.create(sink -> {
            //监听tokenStream
            tokenStream.onPartialResponse((String partialResponse) -> {
                        AiResponseMessage aiResponseMessage = new AiResponseMessage(partialResponse);
                        // .next方法 给新的流写数据
                        sink.next(JSONUtil.toJsonStr(aiResponseMessage));
                    })
                    .onPartialToolExecutionRequest((index, toolExecutionRequest) -> {
                        ToolRequestMessage toolRequestMessage = new ToolRequestMessage(toolExecutionRequest);
                        sink.next(JSONUtil.toJsonStr(toolRequestMessage));
                    })
                    .onToolExecuted((ToolExecution toolExecution) -> {
                        ToolExecutedMessage toolExecutedMessage = new ToolExecutedMessage(toolExecution);
                        sink.next(JSONUtil.toJsonStr(toolExecutedMessage));
                    })
                    .onCompleteResponse((ChatResponse response) -> {
                        if (cancelledRequests.remove(requestId)) {
                            sink.error(new java.util.concurrent.CancellationException("生成请求已取消"));
                            return;
                        }
                        // 执行 Vue 项目构建（同步执行，确保预览时项目已就绪）
                        // TODO 可以考虑使用sse 向前端推送构建进度
                        String projectPath = AppConstant.CODE_OUTPUT_ROOT_DIR + File.separator + "vue_project_" + appId;
                        vueProjectBuilder.buildProject(projectPath);
                        sink.complete();
                    })
                    .onError((Throwable error) -> {
                        cancelledRequests.remove(requestId);
                        error.printStackTrace();
                        sink.error(error);
                    })
                    .start();
        });
    }



    /**
     * 将简单文本 TokenStream 转换为 Flux，并把结束原因检查与产物发布放入完成信号之前。
     */
    private Flux<String> processSimpleTokenStream(TokenStream tokenStream, CodeGenTypeEnum codeGenType, Long appId,
                                                   String requestId) {
        return Flux.create(sink -> {
            StringBuilder content = new StringBuilder();
            tokenStream.onPartialResponse(chunk -> { content.append(chunk); sink.next(chunk); })
                    .onCompleteResponse(response -> {
                        try {
                            if (cancelledRequests.remove(requestId)) {
                                throw new java.util.concurrent.CancellationException("生成请求已取消");
                            }
                            ensureComplete(response.finishReason());
                            if (codeGenType == CodeGenTypeEnum.MULTI_FILE) {
                                artifactPublicationService.publishMultiFile(appId, requestId, content.toString(),
                                        "legacy", response.finishReason() == null ? "" : response.finishReason().name());
                            } else {
                                Object parsed = CodeParserExecutor.executeParser(content.toString(), codeGenType);
                                CodeFileSaverExecutor.executeSaver(parsed, codeGenType, appId);
                            }
                            log.info("AI 产物发布成功, requestId={}, appId={}, type={}, chars={}, finishReason={}",
                                    requestId, appId, codeGenType, content.length(), response.finishReason());
                            sink.complete();
                        } catch (Exception e) {
                            aiCodeGeneratorServiceFactory.resetAfterFailedGeneration(appId, codeGenType);
                            sink.error(e);
                        }
                    })
                    .onError(error -> {
                        cancelledRequests.remove(requestId);
                        // 模型流中途失败也可能留下不完整记忆，必须与校验或发布失败采用相同清理策略。
                        aiCodeGeneratorServiceFactory.resetAfterFailedGeneration(appId, codeGenType);
                        sink.error(error);
                    })
                    .start();
        });
    }

    /**
     * 标记请求已取消；Legacy 模型不支持主动中断，因此完成回调必须据此拒绝构建和发布。
     */
    public void cancelGeneration(String requestId) {
        if (requestId != null && !requestId.isBlank()) cancelledRequests.add(requestId);
    }

    /** 模型因长度或内容过滤结束时拒绝进入解析和发布阶段。 */
    private void ensureComplete(FinishReason finishReason) {
        if (finishReason == FinishReason.LENGTH) {
            throw new com.yupi.yuaicodemother.core.artifact.ArtifactValidationException(
                    "MODEL_OUTPUT_TRUNCATED", null, "模型输出达到长度限制，已保留上一版本");
        }
        if (finishReason == FinishReason.CONTENT_FILTER) {
            throw new com.yupi.yuaicodemother.core.artifact.ArtifactValidationException(
                    "MODEL_OUTPUT_BLOCKED", null, "模型输出被内容策略中止，已保留上一版本");
        }
    }

    /**
     * 生成 HTML 模式的代码并保存
     *
     * @param userMessage 用户提示词
     * @return 保存的目录
     */
    @Deprecated
    private File generateAndSaveHtmlCode(String userMessage) {
        //根据AppId获取相应的App AI Service服务实例
        AiCodeGeneratorService aiCodeGeneratorService = aiCodeGeneratorServiceFactory.getAiCodeGeneratorService(0);
        HtmlCodeResult result = aiCodeGeneratorService.generateHtmlCode(userMessage);
        return CodeFileSaver.saveHtmlCodeResult(result);
    }

    /**
     * 生成多文件模式的代码并保存
     *
     * @param userMessage 用户提示词
     * @return 保存的目录
     */
    @Deprecated
    private File generateAndSaveMultiFileCode(String userMessage) {
        //根据AppId获取相应的App AI Service服务实例
        AiCodeGeneratorService aiCodeGeneratorService = aiCodeGeneratorServiceFactory.getAiCodeGeneratorService(0);
        MultiFileCodeResult result = aiCodeGeneratorService.generateMultiFileCode(userMessage);
        return CodeFileSaver.saveMultiFileCodeResult(result);
    }
}
