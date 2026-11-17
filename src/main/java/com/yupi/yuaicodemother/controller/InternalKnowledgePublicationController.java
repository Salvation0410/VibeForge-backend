package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.common.*;
import com.yupi.yuaicodemother.config.AiEngineProperties;
import com.yupi.yuaicodemother.exception.*;
import com.yupi.yuaicodemother.service.KnowledgePublicationService;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePublicationProtocol.*;
import org.springframework.web.bind.annotation.*;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

@RestController
@RequestMapping("/internal/customer-service/knowledge-publications")
public class InternalKnowledgePublicationController {
    private final KnowledgePublicationService publications;
    private final AiEngineProperties properties;
    public InternalKnowledgePublicationController(KnowledgePublicationService publications,AiEngineProperties properties) {
        this.publications=publications; this.properties=properties;
    }
    @GetMapping("/snapshot")
    public BaseResponse<ReadSnapshot> snapshot(@RequestHeader(value="Authorization",required=false) String authorization) {
        authenticate(authorization);
        return ResultUtils.success(publications.snapshot());
    }
    @GetMapping("/{publicationId}")
    public BaseResponse<PublicationFact> fact(@RequestHeader(value="Authorization",required=false) String authorization,
            @PathVariable String publicationId) {
        authenticate(authorization);
        return ResultUtils.success(publications.fact(publicationId));
    }
    @GetMapping("/inventory")
    public BaseResponse<InventoryPage> inventory(@RequestHeader(value="Authorization",required=false) String authorization,
            @RequestParam(defaultValue="0") long afterEpoch, @RequestParam(defaultValue="100") int limit) {
        authenticate(authorization);
        return ResultUtils.success(publications.inventory(afterEpoch,limit));
    }
    private void authenticate(String authorization) {
        String token=properties.getToken();
        if(token==null || token.isBlank() || authorization==null || !MessageDigest.isEqual(
                ("Bearer "+token).getBytes(StandardCharsets.UTF_8),authorization.getBytes(StandardCharsets.UTF_8)))
            throw new BusinessException(ErrorCode.NO_AUTH_ERROR,"内部认证失败");
    }
}
