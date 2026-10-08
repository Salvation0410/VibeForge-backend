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
     * 向已审核帖子添加评论或嵌套回复。
     */
    Long addComment(CommunityCommentAddRequest request, SysUser loginUser);

    /**
     * 使用游标分页查询帖子或父评论下的评论。
     */
    CursorPage<CommunityCommentVO> listCommentVOByCursor(CommunityCommentQueryRequest request, SysUser loginUserOrNull);

    /**
     * 切换当前用户评论点赞状态，并更新点赞计数。
     */
    CommunityLikeResultVO toggleCommentLike(Long commentId, SysUser loginUser);

    /**
     * 将评论实体转换为前端视图对象。
     */
    List<CommunityCommentVO> getCommentVOList(List<CommunityComment> comments, SysUser loginUserOrNull);

    /**
     * 使用灵活筛选条件为管理端分页查询评论。
     */
    Page<CommunityCommentVO> listCommentVOByPageForAdmin(CommunityCommentAdminQueryRequest request, SysUser adminUser);

    /**
     * 为管理端查询单条评论详情。
     */
    CommunityCommentVO getCommentVOByIdForAdmin(Long commentId, SysUser adminUser);

    /**
     * 软删除评论子树，并修复冗余计数。
     */
    Boolean adminDeleteComment(Long commentId);

    /**
     * 审核评论子树，并修复可见评论的公开计数。
     */
    Boolean reviewComment(CommunityCommentReviewRequest request, SysUser adminUser);
}
