package com.yupi.yuaicodemother.core;

import cn.hutool.json.JSONUtil;
import com.yupi.yuaicodemother.ai.AiCodeGeneratorService;
import com.yupi.yuaicodemother.ai.AiCodeGeneratorServiceFactory;
import com.yupi.yuaicodemother.ai.model.HtmlCodeResult;
import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;
import com.yupi.yuaicodemother.ai.model.message.AiResponseMessage;
import com.yupi.yuaicodemother.ai.model.message.ToolExecutedMessage;
import com.yupi.yuaicodemother.ai.model.message.ToolRequestMessage;
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

/**
 * @author huang
 * @version 1.0
 * @description 调用ai 整合文件写入
 * @date 2026/5/26
 */
@Service
@Slf4j
public class AiCodeGeneratorFacade {
    @Resource
    private AiCodeGeneratorServiceFactory aiCodeGeneratorServiceFactory;

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
        if (codeGenTypeEnum == null) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "生成类型为空");
        }
        //根据AppId获取相应的App AI Service服务实例
        AiCodeGeneratorService aiCodeGeneratorService = aiCodeGeneratorServiceFactory.getAiCodeGeneratorService(appId,codeGenTypeEnum);
        return switch (codeGenTypeEnum) {
            case HTML -> {
                Flux<String> codeStream = aiCodeGeneratorService.generateHtmlCodeStream(userMessage);
                yield processCodeStream(codeStream, CodeGenTypeEnum.HTML,appId);
            }
            case MULTI_FILE -> {
                Flux<String> codeStream = aiCodeGeneratorService.generateMultiFileCodeStream(userMessage);
                yield processCodeStream(codeStream, CodeGenTypeEnum.MULTI_FILE,appId);
            }
            case VUE_PROJECT -> {
               TokenStream tokenStream = aiCodeGeneratorService.generateVueProjectCodeStream(appId, userMessage);
                yield processTokenStream(tokenStream);
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
    private Flux<String> processTokenStream(TokenStream tokenStream) {
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
                        sink.complete();
                    })
                    .onError((Throwable error) -> {
                        error.printStackTrace();
                        sink.error(error);
                    })
                    .start();
        });
    }



    /**
     * 通用流式代码处理方法
     *
     * @param codeStream  代码流
     * @param codeGenType 代码生成类型
     * @param appId 应用Id
     * @return 流式响应
     */
    private Flux<String> processCodeStream(Flux<String> codeStream, CodeGenTypeEnum codeGenType,Long appId) {
        //字符串拼接器 用于当流式返回所有的代码之后 再保存代码
        StringBuilder codeBuilder = new StringBuilder();
        return codeStream.doOnNext(chunk ->{
            //实时收集代码片段
            codeBuilder.append(chunk);
        }).doOnComplete(()->{
            try {
                // 流式输出完毕 拼接代码
                String completeCode = codeBuilder.toString();
                //解析代码 获取解析结果
                Object parseResult = CodeParserExecutor.executeParser(completeCode, codeGenType);
                //保存代码 也是使用解析器
                File savedDir = CodeFileSaverExecutor.executeSaver(parseResult, codeGenType,appId);
                log.info("保存成功，路径为：" + savedDir.getAbsolutePath());
            } catch (Exception e) {
                log.error("代码流解析或保存失败, codeGenType={}, appId={}", codeGenType, appId, e);
            }
        });
    }


    /**
     * 生成 HTML 模式的代码并保存（流式）
     *
     * @param userMessage 用户提示词
     * @return 保存的目录
     */

    @Deprecated
    private Flux<String> generateAndSaveHtmlCodeStream(String userMessage) {

        //根据AppId获取相应的App AI Service服务实例
        AiCodeGeneratorService aiCodeGeneratorService = aiCodeGeneratorServiceFactory.getAiCodeGeneratorService(0);
        //调用AI 返回流式生成对象
        Flux<String> result = aiCodeGeneratorService.generateHtmlCodeStream(userMessage);
        //字符串拼接器 用于当流式返回所有的代码之后 再保存代码
        StringBuilder codeBuilder = new StringBuilder();
        return result.doOnNext(chunk ->{
            //实时收集代码片段
            codeBuilder.append(chunk);
        }).doOnComplete(()->{
            try {
                // 流式输出完毕 保存代码
                String completeHtmlCode = codeBuilder.toString();
                //解析代码
                HtmlCodeResult htmlCodeResult = CodeParser.parseHtmlCode(completeHtmlCode);
                //保存代码
                File saveDir = CodeFileSaver.saveHtmlCodeResult(htmlCodeResult);
                log.info("保存成功 保存目录为：{}", saveDir.getAbsolutePath());
            } catch (Exception e) {
                log.error("保存代码失败", e.getMessage());
            }
        });
    }

    /**
     * 生成多文件模式的代码并保存（流式）
     *
     * @param userMessage 用户提示词
     * @return 保存的目录
     */
    @Deprecated
    private Flux<String> generateAndSaveMultiFileCodeStream(String userMessage)
    {
        //根据AppId获取相应的App AI Service服务实例
        AiCodeGeneratorService aiCodeGeneratorService = aiCodeGeneratorServiceFactory.getAiCodeGeneratorService(0);
        //调AI 返回流式对象
        Flux<String> codeFlux = aiCodeGeneratorService.generateMultiFileCodeStream(userMessage);
        // 拼接字符串
        StringBuilder codeBuilder = new StringBuilder();
        return codeFlux.doOnNext(chunk ->{
            //实时收集代码片段
            codeBuilder.append(chunk);
        }).doOnComplete(()->{
            try {
                // 保存代码
                String completeMultiFileResult = codeBuilder.toString();
                // 解析代码
                MultiFileCodeResult multiFileCodeResult = CodeParser.parseMultiFileCode(completeMultiFileResult);
                //保存代码
                File saveDir = CodeFileSaver.saveMultiFileCodeResult(multiFileCodeResult);
                log.info("保存成功 保存目录为：{}", saveDir.getAbsolutePath());
            } catch (Exception e) {
                log.error("保存代码失败", e.getMessage());
            }
        });

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
