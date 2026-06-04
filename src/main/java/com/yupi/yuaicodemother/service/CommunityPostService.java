package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.common.CursorPage;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostAddRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostAdminQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostPinRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostReviewRequest;
import com.yupi.yuaicodemother.model.entity.CommunityPost;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.CommunityLikeResultVO;
import com.yupi.yuaicodemother.model.vo.CommunityPostVO;
import org.springframework.web.multipart.MultipartFile;

import java.util.List;

public interface CommunityPostService extends IService<CommunityPost> {

    /**
     * Creates a post with optional images. New posts are pending review.
     */
    Long addPost(CommunityPostAddRequest request, List<MultipartFile> imageFiles, SysUser loginUser);

    /**
     * Lists approved posts with cursor pagination for the public square.
     */
    CursorPage<CommunityPostVO> listPostVOByCursor(CommunityPostQueryRequest request, SysUser loginUserOrNull);

    /**
     * Lists all posts created by the current user with cursor pagination.
     */
    CursorPage<CommunityPostVO> listMyPostVOByCursor(CommunityPostQueryRequest request, SysUser loginUser);

    /**
     * Lists approved posts created by the target user for public profile pages.
     */
    CursorPage<CommunityPostVO> listUserPostVOByCursor(Long userId, CommunityPostQueryRequest request, SysUser loginUserOrNull);

    /**
     * Gets post detail while respecting public and owner/admin visibility.
     */
    CommunityPostVO getPostVOById(Long postId, SysUser loginUserOrNull);

    /**
     * Lists all posts for admins with regular pagination.
     */
    Page<CommunityPostVO> listPostVOByPageForAdmin(CommunityPostAdminQueryRequest request, SysUser adminUser);

    /**
     * Toggles current user's like and updates the denormalized like counter.
     */
    CommunityLikeResultVO togglePostLike(Long postId, SysUser loginUser);

    /**
     * Reviews a pending post as approved or rejected.
     */
    Boolean reviewPost(CommunityPostReviewRequest request, SysUser adminUser);

    /**
     * Pins or unpins an approved post created by the admin user.
     */
    Boolean pinPost(CommunityPostPinRequest request, SysUser adminUser);

    /**
     * Converts post entities to frontend view objects.
     */
    List<CommunityPostVO> getPostVOList(List<CommunityPost> posts, SysUser loginUserOrNull);

    /**
     * Converts one post entity to frontend view object.
     */
    CommunityPostVO getPostVO(CommunityPost post, SysUser loginUserOrNull);
}
