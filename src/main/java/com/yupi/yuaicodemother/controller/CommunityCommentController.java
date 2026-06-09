package com.yupi.yuaicodemother.controller;

import com.yupi.yuaicodemother.annotation.AuthCheck;
import com.yupi.yuaicodemother.common.BaseResponse;
import com.yupi.yuaicodemother.common.CursorPage;
import com.yupi.yuaicodemother.common.ResultUtils;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.mybatisflex.core.paginate.Page;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentAddRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentAdminQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentReviewRequest;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.CommunityCommentVO;
import com.yupi.yuaicodemother.model.vo.CommunityLikeResultVO;
import com.yupi.yuaicodemother.service.CommunityCommentService;
import com.yupi.yuaicodemother.service.SysUserService;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/community/comments")
@RequiredArgsConstructor
public class CommunityCommentController {

    private final CommunityCommentService communityCommentService;
    private final SysUserService sysUserService;

    /**
     * 发布帖子评论或嵌套回复。
     */
    @AuthCheck
    @PostMapping("/add")
    public BaseResponse<Long> addComment(@RequestBody CommunityCommentAddRequest commentAddRequest,
                                         HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityCommentService.addComment(commentAddRequest, loginUser));
    }

    /**
     * 游标分页查询顶层评论或某条评论的子评论。
     */
    @GetMapping("/page")
    public BaseResponse<CursorPage<CommunityCommentVO>> listCommentByCursor(CommunityCommentQueryRequest commentQueryRequest,
                                                                           HttpServletRequest request) {
        SysUser loginUser = getLoginUserOrNull(request);
        return ResultUtils.success(communityCommentService.listCommentVOByCursor(commentQueryRequest, loginUser));
    }

    /**
     * 点赞或取消点赞评论。
     */
    @AuthCheck
    @PostMapping("/{id}/like")
    public BaseResponse<CommunityLikeResultVO> toggleCommentLike(@PathVariable Long id, HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityCommentService.toggleCommentLike(id, loginUser));
    }

    /**
     * 管理员分页查询评论列表，支持按帖子、父评论、用户和关键字筛选。
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @PostMapping("/admin/list/page/vo")
    public BaseResponse<Page<CommunityCommentVO>> listCommentByPageForAdmin(@RequestBody CommunityCommentAdminQueryRequest queryRequest,
                                                                            HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityCommentService.listCommentVOByPageForAdmin(queryRequest, loginUser));
    }

    /**
     * 管理员查看评论详情。
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @GetMapping("/admin/{id}")
    public BaseResponse<CommunityCommentVO> getCommentByIdForAdmin(@PathVariable Long id, HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityCommentService.getCommentVOByIdForAdmin(id, loginUser));
    }

    /**
     * 管理员删除评论，删除时会一并软删除整棵回复子树。
     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @DeleteMapping("/admin/{id}")
    public BaseResponse<Boolean> deleteCommentByAdmin(@PathVariable Long id) {
        return ResultUtils.success(communityCommentService.adminDeleteComment(id));
    }

    /**
     * 绠＄悊鍛樺鏍歌瘎璁猴紝浼氭寜璇勮瀛愭爲鎵归噺閫氳繃鎴栭┏鍥炪€?     */
    @AuthCheck(mustRole = UserConstant.ADMIN_ROLE)
    @PostMapping("/admin/review")
    public BaseResponse<Boolean> reviewComment(@RequestBody CommunityCommentReviewRequest reviewRequest,
                                               HttpServletRequest request) {
        SysUser loginUser = sysUserService.getLoginUser(request);
        return ResultUtils.success(communityCommentService.reviewComment(reviewRequest, loginUser));
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
