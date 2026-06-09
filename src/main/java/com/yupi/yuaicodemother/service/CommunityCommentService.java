package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.common.CursorPage;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentAddRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentAdminQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentReviewRequest;
import com.yupi.yuaicodemother.model.entity.CommunityComment;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.CommunityCommentVO;
import com.yupi.yuaicodemother.model.vo.CommunityLikeResultVO;

import java.util.List;

public interface CommunityCommentService extends IService<CommunityComment> {

    /**
     * Adds a comment or nested reply to an approved post.
     */
    Long addComment(CommunityCommentAddRequest request, SysUser loginUser);

    /**
     * Lists comments under a post or parent comment with cursor pagination.
     */
    CursorPage<CommunityCommentVO> listCommentVOByCursor(CommunityCommentQueryRequest request, SysUser loginUserOrNull);

    /**
     * Toggles current user's comment like and updates the like counter.
     */
    CommunityLikeResultVO toggleCommentLike(Long commentId, SysUser loginUser);

    /**
     * Converts comment entities to frontend view objects.
     */
    List<CommunityCommentVO> getCommentVOList(List<CommunityComment> comments, SysUser loginUserOrNull);

    /**
     * Pages comments for the admin console with flexible filters.
     */
    Page<CommunityCommentVO> listCommentVOByPageForAdmin(CommunityCommentAdminQueryRequest request, SysUser adminUser);

    /**
     * Gets a single comment detail for the admin console.
     */
    CommunityCommentVO getCommentVOByIdForAdmin(Long commentId, SysUser adminUser);

    /**
     * Soft deletes a comment subtree and repairs denormalized counters.
     */
    Boolean adminDeleteComment(Long commentId);

    /**
     * Reviews a comment subtree and repairs public counters for visible comments.
     */
    Boolean reviewComment(CommunityCommentReviewRequest request, SysUser adminUser);
}
