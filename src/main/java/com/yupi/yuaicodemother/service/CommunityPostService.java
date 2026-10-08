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
     * 创建带可选图片的帖子，新帖子进入待审核状态。
     */
    Long addPost(CommunityPostAddRequest request, List<MultipartFile> imageFiles, SysUser loginUser);

    /**
     * 使用游标分页查询社区广场中的已审核帖子。
     */
    CursorPage<CommunityPostVO> listPostVOByCursor(CommunityPostQueryRequest request, SysUser loginUserOrNull);

    /**
     * 使用游标分页查询当前用户创建的全部帖子。
     */
    CursorPage<CommunityPostVO> listMyPostVOByCursor(CommunityPostQueryRequest request, SysUser loginUser);

    /**
     * 查询目标用户创建的已审核帖子，供公开个人主页使用。
     */
    CursorPage<CommunityPostVO> listUserPostVOByCursor(Long userId, CommunityPostQueryRequest request, SysUser loginUserOrNull);

    /**
     * 按公开访问、作者和管理员权限查询帖子详情。
     */
    CommunityPostVO getPostVOById(Long postId, SysUser loginUserOrNull);

    /**
     * 使用普通分页查询管理员可见的全部帖子。
     */
    Page<CommunityPostVO> listPostVOByPageForAdmin(CommunityPostAdminQueryRequest request, SysUser adminUser);

    /**
     * 切换当前用户点赞状态，并更新冗余点赞计数。
     */
    CommunityLikeResultVO togglePostLike(Long postId, SysUser loginUser);

    /**
     * 审核待处理帖子并设置通过或拒绝结果。
     */
    Boolean reviewPost(CommunityPostReviewRequest request, SysUser adminUser);

    /**
     * 置顶或取消置顶管理员创建的已审核帖子。
     */
    Boolean pinPost(CommunityPostPinRequest request, SysUser adminUser);

    /**
     * 将帖子实体转换为前端视图对象。
     */
    List<CommunityPostVO> getPostVOList(List<CommunityPost> posts, SysUser loginUserOrNull);

    /**
     * 将单个帖子实体转换为前端视图对象。
     */
    CommunityPostVO getPostVO(CommunityPost post, SysUser loginUserOrNull);
}
