package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.util.StrUtil;
import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.common.CursorPage;
import com.yupi.yuaicodemother.common.PageRequest;
import com.yupi.yuaicodemother.enums.CommunityPostStatusEnum;
import com.yupi.yuaicodemother.enums.CommunitySortTypeEnum;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.mapper.CommunityCommentMapper;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentAddRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentAdminQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityCommentQueryRequest;
import com.yupi.yuaicodemother.model.entity.CommunityComment;
import com.yupi.yuaicodemother.model.entity.CommunityCommentLike;
import com.yupi.yuaicodemother.model.entity.CommunityPost;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.CommunityCommentVO;
import com.yupi.yuaicodemother.model.vo.CommunityLikeResultVO;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.CommunityCommentLikeService;
import com.yupi.yuaicodemother.service.CommunityCommentService;
import com.yupi.yuaicodemother.service.CommunityPostService;
import com.yupi.yuaicodemother.service.SysUserService;
import com.yupi.yuaicodemother.utils.CommunityCursorUtils;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

@Service
@RequiredArgsConstructor
public class CommunityCommentServiceImpl extends ServiceImpl<CommunityCommentMapper, CommunityComment>
        implements CommunityCommentService {

    private static final int MAX_PAGE_SIZE = 20;
    private static final int MAX_ADMIN_PAGE_SIZE = 50;
    private static final Set<String> ADMIN_SORT_FIELDS = Set.of("createTime", "likeCount", "replyCount", "depth", "id");

    private final CommunityPostService communityPostService;
    private final CommunityCommentLikeService communityCommentLikeService;
    private final SysUserService sysUserService;

    @Override
    @Transactional
    public Long addComment(CommunityCommentAddRequest request, SysUser loginUser) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "评论参数不能为空");
        ThrowUtils.throwIf(loginUser == null, ErrorCode.NOT_LOGIN_ERROR);
        ThrowUtils.throwIf(request.getPostId() == null || request.getPostId() <= 0,
                ErrorCode.PARAMS_ERROR, "帖子 ID 不合法");
        ThrowUtils.throwIf(StrUtil.isBlank(request.getContent()), ErrorCode.PARAMS_ERROR, "评论内容不能为空");
        ThrowUtils.throwIf(request.getContent().length() > 2000, ErrorCode.PARAMS_ERROR, "评论内容最多 2000 字");
        CommunityPost post = communityPostService.getById(request.getPostId());
        ThrowUtils.throwIf(post == null, ErrorCode.NOT_FOUND_ERROR, "帖子不存在");
        ThrowUtils.throwIf(!CommunityPostStatusEnum.APPROVED.getValue().equals(post.getStatus()),
                ErrorCode.NO_AUTH_ERROR, "只能评论已审核通过的帖子");

        CommunityComment parent = null;
        Long parentId = Optional.ofNullable(request.getParentId()).orElse(0L);
        if (parentId > 0) {
            parent = this.getById(parentId);
            ThrowUtils.throwIf(parent == null || !parent.getPostId().equals(request.getPostId()),
                    ErrorCode.PARAMS_ERROR, "父评论不存在");
        }

        CommunityComment comment = new CommunityComment();
        comment.setPostId(request.getPostId());
        comment.setUserId(loginUser.getId());
        comment.setParentId(parentId);
        comment.setContent(request.getContent());
        comment.setLikeCount(0);
        comment.setReplyCount(0);
        // 先保存以拿到雪花 ID，再回写 rootId/path，支持无限层级按需查询。
        if (parent == null) {
            comment.setRootId(0L);
            comment.setDepth(0);
            comment.setPath("");
        } else {
            comment.setRootId(parent.getRootId() == null || parent.getRootId() <= 0 ? parent.getId() : parent.getRootId());
            comment.setDepth(Optional.ofNullable(parent.getDepth()).orElse(0) + 1);
            comment.setPath(parent.getPath());
        }
        boolean saved = this.save(comment);
        ThrowUtils.throwIf(!saved, ErrorCode.OPERATION_ERROR, "评论失败");

        CommunityComment updateComment = new CommunityComment();
        updateComment.setId(comment.getId());
        if (parent == null) {
            updateComment.setRootId(comment.getId());
            updateComment.setPath("/" + comment.getId() + "/");
        } else {
            updateComment.setPath(parent.getPath() + comment.getId() + "/");
            incrementReplyCount(parent);
        }
        this.updateById(updateComment);
        incrementPostCommentCount(post);
        return comment.getId();
    }

    @Override
    public CursorPage<CommunityCommentVO> listCommentVOByCursor(CommunityCommentQueryRequest request, SysUser loginUserOrNull) {
        ThrowUtils.throwIf(request == null || request.getPostId() == null || request.getPostId() <= 0,
                ErrorCode.PARAMS_ERROR, "帖子 ID 不合法");
        int pageSize = normalizePageSize(request.getPageSize());
        CommunitySortTypeEnum sortType = CommunitySortTypeEnum.getEnumByValue(request.getSortType());
        ThrowUtils.throwIf(sortType == null, ErrorCode.PARAMS_ERROR, "不支持的排序类型");

        QueryWrapper queryWrapper = buildCommentQuery(request, sortType);
        Page<CommunityComment> page = this.page(Page.of(1, pageSize + 1), queryWrapper);
        List<CommunityComment> comments = page.getRecords();
        boolean hasMore = comments.size() > pageSize;
        if (hasMore) {
            comments = comments.subList(0, pageSize);
        }
        CursorPage<CommunityCommentVO> cursorPage = new CursorPage<>();
        cursorPage.setRecords(getCommentVOList(comments, loginUserOrNull));
        cursorPage.setHasMore(hasMore);
        cursorPage.setNextCursor(hasMore ? buildNextCursor(sortType, comments.get(comments.size() - 1)) : null);
        return cursorPage;
    }

    @Override
    @Transactional
    public CommunityLikeResultVO toggleCommentLike(Long commentId, SysUser loginUser) {
        ThrowUtils.throwIf(commentId == null || commentId <= 0, ErrorCode.PARAMS_ERROR, "评论 ID 不合法");
        ThrowUtils.throwIf(loginUser == null, ErrorCode.NOT_LOGIN_ERROR);
        CommunityComment comment = this.getById(commentId);
        ThrowUtils.throwIf(comment == null, ErrorCode.NOT_FOUND_ERROR, "评论不存在");

        QueryWrapper likeQuery = QueryWrapper.create()
                .eq("commentId", commentId)
                .eq("userId", loginUser.getId());
        CommunityCommentLike oldLike = communityCommentLikeService.getOne(likeQuery);
        boolean liked;
        int likeCount = Optional.ofNullable(comment.getLikeCount()).orElse(0);
        if (oldLike == null) {
            CommunityCommentLike deletedLike = communityCommentLikeService.getOneIncludingDeleted(commentId, loginUser.getId());
            if (deletedLike != null) {
                communityCommentLikeService.restoreLikeById(deletedLike.getId());
            } else {
                communityCommentLikeService.save(CommunityCommentLike.builder().commentId(commentId).userId(loginUser.getId()).build());
            }
            liked = true;
            likeCount++;
        } else {
            communityCommentLikeService.removeById(oldLike.getId());
            liked = false;
            likeCount = Math.max(0, likeCount - 1);
        }
        // 评论点赞数直接参与最热排序，因此同步维护冗余计数。
        CommunityComment updateComment = new CommunityComment();
        updateComment.setId(commentId);
        updateComment.setLikeCount(likeCount);
        this.updateById(updateComment);
        return new CommunityLikeResultVO(liked, likeCount);
    }

    @Override
    public List<CommunityCommentVO> getCommentVOList(List<CommunityComment> comments, SysUser loginUserOrNull) {
        if (CollUtil.isEmpty(comments)) {
            return new ArrayList<>();
        }
        Set<Long> userIds = comments.stream().map(CommunityComment::getUserId).filter(Objects::nonNull).collect(Collectors.toSet());
        Set<Long> commentIds = comments.stream().map(CommunityComment::getId).collect(Collectors.toSet());
        Map<Long, SysUserVO> userMap = userIds.isEmpty() ? new HashMap<>() : sysUserService.listByIds(userIds)
                .stream().collect(Collectors.toMap(SysUser::getId, sysUserService::getSysUserVO));
        Set<Long> likedCommentIds = listLikedCommentIds(commentIds, loginUserOrNull);
        return comments.stream().map(comment -> {
            CommunityCommentVO vo = new CommunityCommentVO();
            BeanUtil.copyProperties(comment, vo);
            vo.setUser(userMap.get(comment.getUserId()));
            vo.setLiked(likedCommentIds.contains(comment.getId()));
            return vo;
        }).toList();
    }

    @Override
    public Page<CommunityCommentVO> listCommentVOByPageForAdmin(CommunityCommentAdminQueryRequest request, SysUser adminUser) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "评论查询参数不能为空");
        int pageNum = request.getPageNum() <= 0 ? 1 : request.getPageNum();
        int pageSize = normalizeAdminPageSize(request.getPageSize());
        QueryWrapper queryWrapper = buildAdminCommentQuery(request);
        Page<CommunityComment> commentPage = this.page(Page.of(pageNum, pageSize), queryWrapper);
        Page<CommunityCommentVO> voPage = new Page<>(commentPage.getPageNumber(), commentPage.getPageSize(), commentPage.getTotalRow());
        voPage.setRecords(getCommentVOList(commentPage.getRecords(), adminUser));
        return voPage;
    }

    @Override
    public CommunityCommentVO getCommentVOByIdForAdmin(Long commentId, SysUser adminUser) {
        ThrowUtils.throwIf(commentId == null || commentId <= 0, ErrorCode.PARAMS_ERROR, "评论 ID 不合法");
        CommunityComment comment = this.getById(commentId);
        ThrowUtils.throwIf(comment == null, ErrorCode.NOT_FOUND_ERROR, "评论不存在");
        return getCommentVOList(List.of(comment), adminUser).get(0);
    }

    @Override
    @Transactional
    public Boolean adminDeleteComment(Long commentId) {
        ThrowUtils.throwIf(commentId == null || commentId <= 0, ErrorCode.PARAMS_ERROR, "评论 ID 不合法");
        CommunityComment comment = this.getById(commentId);
        ThrowUtils.throwIf(comment == null, ErrorCode.NOT_FOUND_ERROR, "评论不存在");

        String commentPath = Optional.ofNullable(comment.getPath()).orElse("");
        QueryWrapper subtreeQuery = QueryWrapper.create().and("path like ?", commentPath + "%");
        // 显式保留 QueryWrapper 类型，兼容 IDEA 对 IService#list 重载的静态分析。
        List<CommunityComment> subtreeComments = this.list((QueryWrapper) subtreeQuery);
        List<Long> deletedCommentIds = subtreeComments.stream().map(CommunityComment::getId).toList();
        int deletedCount = deletedCommentIds.isEmpty() ? 1 : deletedCommentIds.size();

        // 管理后台删除评论时按整棵回复子树软删除，避免留下前端无法展开的孤儿评论。
        boolean removed = deletedCommentIds.isEmpty() ? this.removeById(commentId) : this.removeByIds(deletedCommentIds);
        ThrowUtils.throwIf(!removed, ErrorCode.OPERATION_ERROR, "删除评论失败");

        repairParentReplyCount(comment.getParentId());
        repairPostCommentCount(comment.getPostId(), deletedCount);
        return true;
    }

    private QueryWrapper buildCommentQuery(CommunityCommentQueryRequest request, CommunitySortTypeEnum sortType) {
        Long parentId = Optional.ofNullable(request.getParentId()).orElse(0L);
        QueryWrapper queryWrapper = QueryWrapper.create()
                .eq("postId", request.getPostId())
                .eq("parentId", parentId);
        CommunityCursorUtils.CursorPayload cursor = CommunityCursorUtils.decodeCursor(request.getCursor());
        // 评论游标和排序字段保持一致，避免同赞数或同时间评论翻页重复。
        if (cursor.getLastId() != null && cursor.getLastCreateTime() != null) {
            if (CommunitySortTypeEnum.HOT.equals(sortType)) {
                queryWrapper.and("(likeCount < ? or (likeCount = ? and createTime < ?) or (likeCount = ? and createTime = ? and id < ?))",
                        cursor.getLastLikeCount(), cursor.getLastLikeCount(), cursor.getLastCreateTime(),
                        cursor.getLastLikeCount(), cursor.getLastCreateTime(), cursor.getLastId());
            } else {
                queryWrapper.and("(createTime < ? or (createTime = ? and id < ?))",
                        cursor.getLastCreateTime(), cursor.getLastCreateTime(), cursor.getLastId());
            }
        }
        if (CommunitySortTypeEnum.HOT.equals(sortType)) {
            queryWrapper.orderBy("likeCount", false).orderBy("createTime", false).orderBy("id", false);
        } else {
            queryWrapper.orderBy("createTime", false).orderBy("id", false);
        }
        return queryWrapper;
    }

    private String buildNextCursor(CommunitySortTypeEnum sortType, CommunityComment lastComment) {
        if (CommunitySortTypeEnum.HOT.equals(sortType)) {
            return CommunityCursorUtils.encodeHotCursor(lastComment.getLikeCount(), lastComment.getCreateTime(), lastComment.getId());
        }
        return CommunityCursorUtils.encodeLatestCursor(lastComment.getCreateTime(), lastComment.getId());
    }

    private int normalizePageSize(Integer pageSize) {
        int normalized = pageSize == null ? 10 : pageSize;
        ThrowUtils.throwIf(normalized <= 0 || normalized > MAX_PAGE_SIZE, ErrorCode.PARAMS_ERROR, "pageSize 必须在 1-20 之间");
        return normalized;
    }

    private Set<Long> listLikedCommentIds(Set<Long> commentIds, SysUser loginUserOrNull) {
        if (loginUserOrNull == null || commentIds.isEmpty()) {
            return new HashSet<>();
        }
        QueryWrapper queryWrapper = QueryWrapper.create()
                .in("commentId", commentIds)
                .eq("userId", loginUserOrNull.getId());
        return communityCommentLikeService.list(queryWrapper).stream()
                .map(CommunityCommentLike::getCommentId)
                .collect(Collectors.toSet());
    }

    private void incrementPostCommentCount(CommunityPost post) {
        CommunityPost updatePost = new CommunityPost();
        updatePost.setId(post.getId());
        updatePost.setCommentCount(Optional.ofNullable(post.getCommentCount()).orElse(0) + 1);
        communityPostService.updateById(updatePost);
    }

    private void incrementReplyCount(CommunityComment parent) {
        CommunityComment updateParent = new CommunityComment();
        updateParent.setId(parent.getId());
        updateParent.setReplyCount(Optional.ofNullable(parent.getReplyCount()).orElse(0) + 1);
        this.updateById(updateParent);
    }

    private QueryWrapper buildAdminCommentQuery(CommunityCommentAdminQueryRequest request) {
        QueryWrapper queryWrapper = QueryWrapper.create();
        if (request.getPostId() != null && request.getPostId() > 0) {
            queryWrapper.eq("postId", request.getPostId());
        }
        if (request.getParentId() != null && request.getParentId() >= 0) {
            queryWrapper.eq("parentId", request.getParentId());
        }
        if (request.getRootId() != null && request.getRootId() > 0) {
            queryWrapper.eq("rootId", request.getRootId());
        }
        if (request.getUserId() != null && request.getUserId() > 0) {
            queryWrapper.eq("userId", request.getUserId());
        }
        if (StrUtil.isNotBlank(request.getKeyword())) {
            queryWrapper.like("content", request.getKeyword().trim());
        }
        applyAdminSort(queryWrapper, request);
        return queryWrapper;
    }

    private int normalizeAdminPageSize(int pageSize) {
        int normalized = pageSize <= 0 ? 10 : pageSize;
        ThrowUtils.throwIf(normalized > MAX_ADMIN_PAGE_SIZE, ErrorCode.PARAMS_ERROR, "pageSize 不能超过 50");
        return normalized;
    }

    /**
     * 后台列表只放行白名单排序字段，避免前端透传任意列名带来 SQL 风险。
     */
    private void applyAdminSort(QueryWrapper queryWrapper, PageRequest pageRequest) {
        String sortField = pageRequest.getSortField();
        if (StrUtil.isNotBlank(sortField) && ADMIN_SORT_FIELDS.contains(sortField)) {
            queryWrapper.orderBy(sortField, "ascend".equalsIgnoreCase(pageRequest.getSortOrder()));
            queryWrapper.orderBy("id", false);
            return;
        }
        queryWrapper.orderBy("createTime", false).orderBy("id", false);
    }

    private void repairParentReplyCount(Long parentId) {
        if (parentId == null || parentId <= 0) {
            return;
        }
        CommunityComment parent = this.getById(parentId);
        if (parent == null) {
            return;
        }
        CommunityComment updateParent = new CommunityComment();
        updateParent.setId(parentId);
        updateParent.setReplyCount(Math.max(0, Optional.ofNullable(parent.getReplyCount()).orElse(0) - 1));
        this.updateById(updateParent);
    }

    /**
     * 评论总数是帖子详情和广场列表直接展示字段，删除子树后需要同步修正冗余计数。
     */
    private void repairPostCommentCount(Long postId, int deletedCount) {
        if (postId == null || postId <= 0 || deletedCount <= 0) {
            return;
        }
        CommunityPost post = communityPostService.getById(postId);
        if (post == null) {
            return;
        }
        CommunityPost updatePost = new CommunityPost();
        updatePost.setId(postId);
        updatePost.setCommentCount(Math.max(0, Optional.ofNullable(post.getCommentCount()).orElse(0) - deletedCount));
        communityPostService.updateById(updatePost);
    }
}
