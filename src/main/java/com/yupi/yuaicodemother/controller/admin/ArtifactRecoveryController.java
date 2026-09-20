package com.yupi.yuaicodemother.controller.admin;

import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.core.artifact.ArtifactPublicationService;
import com.yupi.yuaicodemother.core.artifact.ArtifactValidationError;
import com.yupi.yuaicodemother.core.artifact.HtmlSmokeTestResult;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.model.dto.app.HtmlArtifactRecoveryRequest;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/** 管理员显式校验或发布人工审核过的 HTML 恢复候选。 */
@RestController
@RequestMapping("/apps/admin/artifacts/html")
@RequiredArgsConstructor
@Slf4j
public class ArtifactRecoveryController {
    private static final String RECOVERY_ENGINE = "manual-recovery";
    private static final String RECOVERY_FINISH_REASON = "MANUAL_RECOVERY";
    private final ArtifactPublicationService publicationService;

    /**
     * 默认只运行解析、确定性校验和浏览器烟测；仅 dryRun=false 时创建新 release。
     * 候选和来源必须由管理员显式提供，本方法不会搜索聊天历史或读取生成项目目录。
     */
    @PostMapping("/recover")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<HtmlArtifactRecoveryResponse> recover(@RequestBody HtmlArtifactRecoveryRequest request) {
        validateRequest(request);
        log.info("执行 HTML 人工恢复, appId={}, requestId={}, dryRun={}, source={}",
                request.getAppId(), request.getRequestId(), request.isDryRun(), request.getSourceDescription());
        if (request.isDryRun()) {
            ArtifactPublicationService.HtmlInspectionResult inspection =
                    publicationService.inspectHtml(request.getCandidateHtml());
            return ResultUtils.success(new HtmlArtifactRecoveryResponse(true, inspection.valid(), false,
                    null, Map.of(), inspection.errors(), inspection.smokeTest(), request.getSourceDescription()));
        }

        var published = publicationService.publishHtml(request.getAppId(), request.getRequestId(),
                request.getCandidateHtml(), RECOVERY_ENGINE, RECOVERY_FINISH_REASON);
        return ResultUtils.success(new HtmlArtifactRecoveryResponse(false, true, published.published(),
                published.versionId(), published.hashes(), List.of(), HtmlSmokeTestResult.success(),
                request.getSourceDescription()));
    }

    /** 拒绝缺少人工审核上下文或非法幂等键的请求，失败时不得进入烟测和发布。 */
    private void validateRequest(HtmlArtifactRecoveryRequest request) {
        if (request == null || request.getAppId() == null || request.getAppId() <= 0
                || request.getRequestId() == null
                || !request.getRequestId().matches("[A-Za-z0-9._-]{1,128}")
                || request.getCandidateHtml() == null || request.getCandidateHtml().isBlank()
                || request.getSourceDescription() == null || request.getSourceDescription().isBlank()
                || request.getSourceDescription().length() > 500) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "恢复参数不完整或格式非法");
        }
    }

    /** 返回 dry-run 的校验/烟测明细，或正式提交后的版本与摘要。 */
    public record HtmlArtifactRecoveryResponse(boolean dryRun, boolean valid, boolean published,
                                               String versionId, Map<String, String> hashes,
                                               List<ArtifactValidationError> validationErrors,
                                               HtmlSmokeTestResult smokeTest,
                                               String sourceDescription) {
    }
}
