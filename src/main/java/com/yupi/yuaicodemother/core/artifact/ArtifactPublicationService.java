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
import org.springframework.core.env.Environment;
import org.springframework.core.env.Profiles;
import lombok.extern.slf4j.Slf4j;

import java.nio.file.Path;
import java.nio.file.Files;
import java.nio.file.StandardOpenOption;
import java.util.List;
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
    private final Environment environment;

    /** 组装解析器、确定性校验器、浏览器烟测、共享版本存储和生成提交状态门。 */
    @Autowired
    public ArtifactPublicationService(MultiFileArtifactValidator validator, HtmlArtifactValidator htmlValidator,
                                      ArtifactPathResolver resolver, ObjectMapper objectMapper,
                                      GenerationLeaseService generationLeaseService, HtmlSmokeTester smokeTester,
                                      HtmlArtifactProperties htmlProperties, Environment environment) {
        this.multiFileValidator = validator;
        this.htmlValidator = htmlValidator;
        this.store = new VersionedArtifactStore(resolver, objectMapper);
        this.generationLeaseService = generationLeaseService;
        this.smokeTester = smokeTester;
        this.htmlProperties = htmlProperties;
        this.environment = environment;
    }

    /** 隔离测试构造器；不启用生成取消状态门。 */
    ArtifactPublicationService(MultiFileArtifactValidator validator, ArtifactPathResolver resolver,
                               ObjectMapper objectMapper) {
        this(validator, new HtmlArtifactValidator(), resolver, objectMapper, null,
                path -> HtmlSmokeTestResult.success(), new HtmlArtifactProperties(), null);
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
        if (generationLeaseService != null && "manual-recovery".equals(engine)) {
            var lease = generationLeaseService.acquire(appId, requestId);
            try {
                return publish(CodeGenTypeEnum.HTML, appId, requestId,
                        Map.of("index.html", artifact.getHtmlCode()), engine, finishReason, this::verifyHtmlStaging);
            } finally {
                generationLeaseService.release(lease);
            }
        }
        return publish(CodeGenTypeEnum.HTML, appId, requestId,
                Map.of("index.html", artifact.getHtmlCode()), engine, finishReason, this::verifyHtmlStaging);
    }

    /**
     * 在系统临时目录对人工候选执行与发布一致的解析、确定性校验和烟测，但不创建 release 或修改活动指针。
     * 任一阶段失败都会作为结构化结果返回，临时文件无论成功失败都立即清理。
     */
    public HtmlInspectionResult inspectHtml(String rawArtifact) {
        HtmlCodeResult artifact;
        try {
            artifact = htmlParser.parse(rawArtifact);
        } catch (ArtifactValidationException e) {
            return new HtmlInspectionResult(false,
                    List.of(new ArtifactValidationError(e.getErrorCode(), e.getFile(), e.getMessage())), null);
        }
        ArtifactValidationResult validation = htmlValidator.validate(artifact);
        if (!validation.valid()) {
            return new HtmlInspectionResult(false, validation.errors(), null);
        }

        Path temporaryDirectory = null;
        try {
            temporaryDirectory = Files.createTempDirectory("html-recovery-inspect-");
            Files.writeString(temporaryDirectory.resolve("index.html"), artifact.getHtmlCode(),
                    StandardOpenOption.CREATE_NEW);
            HtmlSmokeTestResult smokeResult = inspectHtmlStaging(temporaryDirectory);
            return new HtmlInspectionResult(smokeResult.passed(), List.of(), smokeResult);
        } catch (Exception e) {
            return new HtmlInspectionResult(false, List.of(), HtmlSmokeTestResult.failure(
                    "HTML_SMOKE_TEST_UNAVAILABLE", "HTML 浏览器烟测不可用: " + e.getMessage()));
        } finally {
            deleteInspectionDirectory(temporaryDirectory);
        }
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
        HtmlSmokeTestResult result = inspectHtmlStaging(staging);
        if (!result.passed()) {
            throw new ArtifactValidationException(result.errorCode(), "index.html", result.message());
        }
    }

    /** 执行统一烟测策略并返回结构化结果，使 dry-run 可以展示失败而不发布。 */
    private HtmlSmokeTestResult inspectHtmlStaging(Path staging) {
        if (!htmlProperties.isEnabled()) {
            if (htmlProperties.isRequired()) {
                return HtmlSmokeTestResult.failure("HTML_SMOKE_TEST_UNAVAILABLE", "HTML 浏览器烟测已禁用，无法验证候选版本");
            }
            if (environment == null || !environment.acceptsProfiles(Profiles.of("local", "dev", "test"))) {
                return HtmlSmokeTestResult.failure("HTML_SMOKE_TEST_UNAVAILABLE",
                        "只有 local、dev 或 test profile 可跳过 HTML 浏览器烟测");
            }
            log.warn("开发 profile 显式跳过 HTML 浏览器烟测: {}", staging);
            return HtmlSmokeTestResult.success();
        }
        try {
            HtmlSmokeTestResult result = smokeTester.verify(staging.resolve("index.html"));
            if (result == null) {
                return HtmlSmokeTestResult.failure("HTML_SMOKE_TEST_UNAVAILABLE", "HTML 浏览器烟测未返回结果");
            }
            if (!result.passed() && (result.errorCode() == null || result.errorCode().isBlank())) {
                return HtmlSmokeTestResult.failure("HTML_SMOKE_TEST_FAILED",
                        result.message() == null ? "HTML 浏览器烟测失败" : result.message());
            }
            return result;
        } catch (Exception e) {
            return HtmlSmokeTestResult.failure("HTML_SMOKE_TEST_UNAVAILABLE", "HTML 浏览器烟测不可用: " + e.getMessage());
        }
    }

    /** 只清理本方法创建的系统临时目录；清理失败降级记录，不改变已得到的校验结果。 */
    private void deleteInspectionDirectory(Path directory) {
        if (directory == null || !Files.exists(directory)) return;
        try (var paths = Files.walk(directory)) {
            for (Path path : paths.sorted(java.util.Comparator.reverseOrder()).toList()) {
                Files.deleteIfExists(path);
            }
        } catch (Exception e) {
            log.warn("清理 HTML 恢复 dry-run 临时目录失败: {}", directory, e);
        }
    }

    /** dry-run 校验结果；确定性错误与烟测结果分开返回，便于运维定位。 */
    public record HtmlInspectionResult(boolean valid, List<ArtifactValidationError> errors,
                                       HtmlSmokeTestResult smokeTest) {
        public HtmlInspectionResult {
            errors = List.copyOf(errors);
        }
    }

    /** 在解析模型输出前校验幂等键，非法请求不得消耗解析或创建任何候选目录。 */
    private void validateRequestId(String requestId) {
        if (requestId == null || !requestId.matches("[A-Za-z0-9._-]{1,128}")) {
            throw new ArtifactValidationException("REQUEST_ID_INVALID", null, "请求 ID 非法");
        }
    }
}
