package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.mybatisflex.core.paginate.Page;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.CursorPage;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostAddRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostAdminQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostPinRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostReviewRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.CommunityLikeResultVO;
import com.yupi.yuaicodemother.model.vo.CommunityPostVO;
import com.yupi.yuaicodemother.service.CommunityPostService;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

@RestController
@RequestMapping("/community/posts")
@RequiredArgsConstructor
public class CommunityPostController {

    private final CommunityPostService communityPostService;
    private final SysUserService sysUserService;

    /**
     * 发布帖子。新帖默认进入待审核状态，不会立即进入广场。
     */
    @AuthCheck
    @PostMapping(value = "/add", consumes = MediaType.MULTIPART_FORM_DATA_VALUE)
    public BaseResponse<Long> addPost(@ModelAttribute CommunityPostAddRequest postAddRequest,
                                      @RequestParam(value = "imageFiles", required = false) List<MultipartFile> imageFiles,
                                      HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityPostService.addPost(postAddRequest, imageFiles, loginUser));
    }

    /**
     * 游标分页查询帖子广场，未登录用户也可以浏览。
     */
    @GetMapping("/page")
    public BaseResponse<CursorPage<CommunityPostVO>> listPostByCursor(CommunityPostQueryRequest postQueryRequest,
                                                                      HttpServletRequest request) {
        SysUser loginUser = getLoginUserOrNull(request);
        return ResultUtils.success(communityPostService.listPostVOByCursor(postQueryRequest, loginUser));
    }

    /**
     * 获取帖子详情。公开只允许看已审核帖子，作者和管理员可看非公开状态。
     */
    @AuthCheck
    @GetMapping("/my/page")
    public BaseResponse<CursorPage<CommunityPostVO>> listMyPostByCursor(CommunityPostQueryRequest postQueryRequest,
                                                                        HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityPostService.listMyPostVOByCursor(postQueryRequest, loginUser));
    }

    @GetMapping("/user/{userId}/page")
    public BaseResponse<CursorPage<CommunityPostVO>> listUserPostByCursor(@PathVariable Long userId,
                                                                          CommunityPostQueryRequest postQueryRequest,
                                                                          HttpServletRequest request) {
        SysUser loginUser = getLoginUserOrNull(request);
        return ResultUtils.success(communityPostService.listUserPostVOByCursor(userId, postQueryRequest, loginUser));
    }

    @GetMapping("/{id}")
    public BaseResponse<CommunityPostVO> getPostById(@PathVariable Long id, HttpServletRequest request) {
        SysUser loginUser = getLoginUserOrNull(request);
        return ResultUtils.success(communityPostService.getPostVOById(id, loginUser));
    }

    /**
     * 点赞或取消点赞帖子。
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @PostMapping("/admin/list/page/vo")
    public BaseResponse<Page<CommunityPostVO>> listPostByPageForAdmin(@RequestBody CommunityPostAdminQueryRequest queryRequest,
                                                                      HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityPostService.listPostVOByPageForAdmin(queryRequest, loginUser));
    }

    @AuthCheck
    @PostMapping("/{id}/like")
    public BaseResponse<CommunityLikeResultVO> togglePostLike(@PathVariable Long id, HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityPostService.togglePostLike(id, loginUser));
    }

    /**
     * 管理员审核帖子。
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @PostMapping("/admin/review")
    public BaseResponse<Boolean> reviewPost(@RequestBody CommunityPostReviewRequest reviewRequest,
                                            HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityPostService.reviewPost(reviewRequest, loginUser));
    }

    /**
     * 管理员置顶或取消置顶自己发布的已审核帖子。
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @PostMapping("/admin/pin")
    public BaseResponse<Boolean> pinPost(@RequestBody CommunityPostPinRequest pinRequest,
                                         HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityPostService.pinPost(pinRequest, loginUser));
    }

    private SysUser getLoginUserOrNull(HttpServletRequest request) {
        try {
            return sysUserService.getLoginUser(request);
        } catch (BusinessException e) {
            if (ErrorCode.NOT_LOGIN_ERROR.getCode() == e.getCode()) {
                return null;
            }
            throw e;
        }
    }
}
