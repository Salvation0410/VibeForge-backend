package com.yupi.yuaicodemother.core.artifact;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.yupi.yuaicodemother.ai.gateway.GenerationLeaseService;
import com.yupi.yuaicodemother.ai.gateway.GenerationStreamException;
import com.yupi.yuaicodemother.ai.model.HtmlCodeResult;
import com.yupi.yuaicodemother.ai.model.MultiFileCodeResult;
import com.yupi.yuaicodemother.config.HtmlArtifactProperties;
import com.yupi.yuaicodemother.core.paser.MultiFileCodeParser;
import com.yupi.yuaicodemother.enums.CodeGenTypeEnum;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import lombok.extern.slf4j.Slf4j;

import java.nio.file.Path;
import java.util.Map;

/** 负责解析、校验并把不同生成类型交给统一的不可变版本存储。 */
@Service
@Slf4j
public class ArtifactPublicationService {
    private final MultiFileCodeParser parser = new MultiFileCodeParser();
    private final HtmlArtifactParser htmlParser = new HtmlArtifactParser();
    private final MultiFileArtifactValidator multiFileValidator;
    private final HtmlArtifactValidator htmlValidator;
    private final VersionedArtifactStore store;
    private final GenerationLeaseService generationLeaseService;
    private final HtmlSmokeTester smokeTester;
    private final HtmlArtifactProperties htmlProperties;

    /** 组装解析器、确定性校验器、浏览器烟测、共享版本存储和生成提交状态门。 */
    @Autowired
    public ArtifactPublicationService(MultiFileArtifactValidator validator, HtmlArtifactValidator htmlValidator,
                                      ArtifactPathResolver resolver, ObjectMapper objectMapper,
                                      GenerationLeaseService generationLeaseService, HtmlSmokeTester smokeTester,
                                      HtmlArtifactProperties htmlProperties) {
        this.multiFileValidator = validator;
        this.htmlValidator = htmlValidator;
        this.store = new VersionedArtifactStore(resolver, objectMapper);
        this.generationLeaseService = generationLeaseService;
        this.smokeTester = smokeTester;
        this.htmlProperties = htmlProperties;
    }

    /** 隔离测试构造器；不启用生成取消状态门。 */
    ArtifactPublicationService(MultiFileArtifactValidator validator, ArtifactPathResolver resolver,
                               ObjectMapper objectMapper) {
        this(validator, new HtmlArtifactValidator(), resolver, objectMapper, null,
                path -> HtmlSmokeTestResult.success(), new HtmlArtifactProperties());
    }

    /** 严格解析并发布三文件产物，发布失败不会改变当前指针。 */
    public ArtifactPublishResult publishMultiFile(long appId, String requestId, String rawArtifact,
                                                  String engine, String finishReason) {
        validateRequestId(requestId);
        MultiFileCodeResult artifact = parser.parseCode(rawArtifact);
        multiFileValidator.validateOrThrow(artifact);
        Map<String, String> files = Map.of("index.html", artifact.getHtmlCode(),
                "style.css", artifact.getCssCode(), "script.js", artifact.getJsCode());
        return publish(CodeGenTypeEnum.MULTI_FILE, appId, requestId, files, engine, finishReason);
    }

    /** 严格解析、校验并发布单文件 HTML；仅在完整产物通过校验后写入版本。 */
    public ArtifactPublishResult publishHtml(long appId, String requestId, String rawArtifact,
                                             String engine, String finishReason) {
        validateRequestId(requestId);
        HtmlCodeResult artifact = htmlParser.parse(rawArtifact);
        htmlValidator.validateOrThrow(artifact);
        return publish(CodeGenTypeEnum.HTML, appId, requestId,
                Map.of("index.html", artifact.getHtmlCode()), engine, finishReason, this::verifyHtmlStaging);
    }

    /** 在取消与提交的原子状态门内执行共享发布，确保只有一个请求获胜。 */
    private ArtifactPublishResult publish(CodeGenTypeEnum type, long appId, String requestId,
                                         Map<String, String> files, String engine, String finishReason) {
        return publish(type, appId, requestId, files, engine, finishReason, null);
    }

    /** 仅 HTML 在 staging 哈希核对后执行烟测，失败时不得移动 release 或活动指针。 */
    private ArtifactPublishResult publish(CodeGenTypeEnum type, long appId, String requestId,
                                         Map<String, String> files, String engine, String finishReason,
                                         VersionedArtifactStore.StagingVerifier verifier) {
        try {
            return generationLeaseService == null
                    ? store.publish(type, appId, requestId, files, engine, finishReason, verifier)
                    : generationLeaseService.commit(appId, requestId,
                    () -> store.publish(type, appId, requestId, files, engine, finishReason, verifier));
        } catch (ArtifactValidationException | GenerationStreamException e) {
            throw e;
        } catch (Exception e) {
            throw new ArtifactValidationException("ARTIFACT_PUBLISH_FAILED", null, "产物发布失败: " + e.getMessage());
        }
    }

    /** 生产默认失败关闭；只有显式关闭 enabled 和 required 才允许开发环境跳过。 */
    private void verifyHtmlStaging(Path staging) {
        if (!htmlProperties.isEnabled()) {
            if (htmlProperties.isRequired()) {
                throw new ArtifactValidationException("HTML_SMOKE_TEST_UNAVAILABLE", "index.html", "HTML 浏览器烟测已禁用，无法验证候选版本");
            }
            log.warn("开发配置跳过 HTML 浏览器烟测: {}", staging);
            return;
        }
        HtmlSmokeTestResult result;
        try {
            result = smokeTester.verify(staging.resolve("index.html"));
        } catch (Exception e) {
            throw new ArtifactValidationException("HTML_SMOKE_TEST_UNAVAILABLE", "index.html", "HTML 浏览器烟测不可用: " + e.getMessage());
        }
        if (result == null || !result.passed()) {
            String code = result == null || result.errorCode() == null
                    ? "HTML_SMOKE_TEST_UNAVAILABLE" : result.errorCode();
            throw new ArtifactValidationException(code, "index.html",
                    result == null ? "HTML 浏览器烟测未返回结果" : result.message());
        }
    }

    /** 在解析模型输出前校验幂等键，非法请求不得消耗解析或创建任何候选目录。 */
    private void validateRequestId(String requestId) {
        if (requestId == null || !requestId.matches("[A-Za-z0-9._-]{1,128}")) {
            throw new ArtifactValidationException("REQUEST_ID_INVALID", null, "请求 ID 非法");
        }
    }
}
