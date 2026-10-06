package com.yupi.yuaicodemother.controller;

import com.mybatisflex.core.paginate.Page;
import com.yupi.yuaicodemother.ai.customerservice.CustomerServiceAiClient;
import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.config.CustomerServiceProperties;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.model.dto.customerservice.KnowledgePageRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.CustomerServiceKnowledgeDocumentVO;
import com.yupi.yuaicodemother.model.vo.CustomerServiceKnowledgeTaskVO;
import com.yupi.yuaicodemother.service.CustomerServiceKnowledgeService;
import com.yupi.yuaicodemother.service.KnowledgeMutationCoordinator;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/admin/customer-service/knowledge")
@RequiredArgsConstructor
public class CustomerServiceKnowledgeAdminController {
    private final CustomerServiceKnowledgeService knowledgeService;
    private final SysUserService sysUserService;
    private final CustomerServiceAiClient aiClient;
    private final KnowledgeMutationCoordinator coordinator;
    private final CustomerServiceProperties properties;

    @PostMapping("/upload")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<CustomerServiceKnowledgeDocumentVO> upload(@RequestPart("file") MultipartFile file,
            @RequestParam(value = "replacementId", required = false) Long replacementId, HttpServletRequest request) {
        return ResultUtils.success(knowledgeService.upload(file, replacementId, userId(request)));
    }

    @PostMapping("/page")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<Page<CustomerServiceKnowledgeDocumentVO>> page(@RequestBody KnowledgePageRequest request) {
        return ResultUtils.success(knowledgeService.page(request.getPageNum(), request.getPageSize()));
    }

    @GetMapping("/{id}")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<CustomerServiceKnowledgeDocumentVO> detail(@PathVariable long id) {
        return ResultUtils.success(knowledgeService.detail(id));
    }

    @PostMapping("/{id}:reindex")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<CustomerServiceKnowledgeDocumentVO> reindex(@PathVariable long id, HttpServletRequest request) {
        return ResultUtils.success(knowledgeService.reindex(id, userId(request)));
    }

    @PostMapping("/{id}:disable")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<CustomerServiceKnowledgeDocumentVO> disable(@PathVariable long id, HttpServletRequest request) {
        return ResultUtils.success(knowledgeService.disable(id, userId(request)));
    }

    @PostMapping("/{id}:enable")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<CustomerServiceKnowledgeDocumentVO> enable(@PathVariable long id, HttpServletRequest request) {
        return ResultUtils.success(knowledgeService.enable(id, userId(request)));
    }

    @DeleteMapping("/{id}")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<Boolean> delete(@PathVariable long id, HttpServletRequest request) {
        return ResultUtils.success(knowledgeService.delete(id, userId(request)));
    }

    @GetMapping("/{id}/tasks")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<List<CustomerServiceKnowledgeTaskVO>> tasks(@PathVariable long id,
            @RequestParam(defaultValue = "20") int limit) {
        return ResultUtils.success(knowledgeService.taskHistory(id, limit));
    }

    @PostMapping("/rebuild")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<Integer> rebuild(HttpServletRequest request) {
        return ResultUtils.success(knowledgeService.rebuild(userId(request)));
    }

    @GetMapping("/health")
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    public BaseResponse<Map<String, Boolean>> health() {
        return ResultUtils.success(Map.of("enabled", properties.isEnabled(),
                "coordinator", coordinator.ready(), "python", properties.isEnabled() && aiClient.health()));
    }

    private long userId(HttpServletRequest request) {
        SysUser user = sysUserService.getLoginUser(request);
        return user.getId();
    }
}
