package com.yupi.yuaicodemother.controller;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.Map;

@RestController
@RequestMapping("/internal/customer-service/knowledge-mutation-leases")
@RequiredArgsConstructor
public class InternalCustomerServiceKnowledgeController {
    private final KnowledgeMutationCoordinator coordinator;
    private final AiEngineProperties aiProperties;

    @PostMapping(":validate")
    public BaseResponse<KnowledgeMutationCoordinator.Validation> validate(
            @RequestHeader(value = "Authorization", required = false) String authorization,
            @RequestBody LeaseValidationRequest request) {
        authenticate(authorization);
        return ResultUtils.success(coordinator.validate(new KnowledgeMutationCoordinator.Lease(
                request.scope(), request.operationId(), request.operation(), request.fence(),
                request.expiresAt(), request.proof())));
    }

    @GetMapping("/health")
    public BaseResponse<Map<String, Boolean>> health(
            @RequestHeader(value = "Authorization", required = false) String authorization) {
        authenticate(authorization);
        if (!coordinator.ready()) throw new BusinessException(ErrorCode.SYSTEM_ERROR, "Knowledge mutation coordinator is not ready");
        return ResultUtils.success(Map.of("ready", true));
    }

    private void authenticate(String authorization) {
        String token = aiProperties.getToken();
        String expected = "Bearer " + token;
        if (token == null || token.isBlank() || authorization == null || !MessageDigest.isEqual(
                expected.getBytes(StandardCharsets.UTF_8), authorization.getBytes(StandardCharsets.UTF_8)))
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR, "Invalid internal bearer token");
    }

    @JsonIgnoreProperties(ignoreUnknown = false)
    public record LeaseValidationRequest(String scope, String operationId, String operation, long fence,
                                         long expiresAt, String proof) { }
}
