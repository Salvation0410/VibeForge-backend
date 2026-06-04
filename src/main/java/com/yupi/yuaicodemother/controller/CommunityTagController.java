package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.model.dto.community.CommunityTagSaveRequest;
import com.yupi.yuaicodemother.model.vo.CommunityTagVO;
import com.yupi.yuaicodemother.service.CommunityTagService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;

@RestController
@RequestMapping("/community/tags")
@RequiredArgsConstructor
public class CommunityTagController {

    private final CommunityTagService communityTagService;

    @GetMapping
    public BaseResponse<List<CommunityTagVO>> listEnabledTags() {
        return ResultUtils.success(communityTagService.listEnabledTags());
    }

    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @GetMapping("/admin/all")
    public BaseResponse<List<CommunityTagVO>> listAllTags() {
        return ResultUtils.success(communityTagService.listAllTags());
    }

    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @PostMapping("/admin/save")
    public BaseResponse<Boolean> saveTag(@RequestBody CommunityTagSaveRequest tagSaveRequest) {
        return ResultUtils.success(communityTagService.saveTag(tagSaveRequest));
    }

    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @DeleteMapping("/admin/{id}")
    public BaseResponse<Boolean> deleteTag(@PathVariable Long id) {
        return ResultUtils.success(communityTagService.removeById(id));
    }
}
