package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.collection.CollUtil;
import cn.hutool.core.util.StrUtil;
import com.mybatisflex.core.paginate.Page;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.common.CursorPage;
import com.yupi.yuaicodemother.constant.UserConstant;
import com.yupi.yuaicodemother.enums.CommunityPostStatusEnum;
import com.yupi.yuaicodemother.enums.CommunitySortTypeEnum;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.manager.OssManager;
import com.yupi.yuaicodemother.mapper.CommunityPostMapper;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostAddRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostAdminQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostPinRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostQueryRequest;
import com.yupi.yuaicodemother.model.dto.community.CommunityPostReviewRequest;
import com.yupi.yuaicodemother.model.entity.CommunityPost;
import com.yupi.yuaicodemother.model.entity.CommunityPostImage;
import com.yupi.yuaicodemother.model.entity.CommunityPostLike;
import com.yupi.yuaicodemother.model.entity.CommunityTag;
import com.yupi.yuaicodemother.model.entity.SysUser;
import com.yupi.yuaicodemother.model.vo.CommunityLikeResultVO;
import com.yupi.yuaicodemother.model.vo.CommunityPostImageVO;
import com.yupi.yuaicodemother.model.vo.CommunityPostVO;
import com.yupi.yuaicodemother.model.vo.CommunityTagVO;
import com.yupi.yuaicodemother.model.vo.SysUserVO;
import com.yupi.yuaicodemother.service.CommunityPostImageService;
import com.yupi.yuaicodemother.service.CommunityPostLikeService;
import com.yupi.yuaicodemother.service.CommunityPostService;
import com.yupi.yuaicodemother.service.CommunityTagService;
import com.yupi.yuaicodemother.service.SysUserService;
import com.yupi.yuaicodemother.utils.CommunityCursorUtils;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.multipart.MultipartFile;

import java.time.LocalDateTime;
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
public class CommunityPostServiceImpl extends ServiceImpl<CommunityPostMapper, CommunityPost>
        implements CommunityPostService {

    private static final int MAX_PAGE_SIZE = 20;
    private static final int MAX_ADMIN_PAGE_SIZE = 50;
    private static final int MAX_IMAGE_COUNT = 9;
    private static final Set<String> ADMIN_SORT_FIELDS = Set.of("createTime", "likeCount", "commentCount", "id", "pinnedTime");

    private final CommunityTagService communityTagService;
    private final CommunityPostImageService communityPostImageService;
    private final CommunityPostLikeService communityPostLikeService;
    private final SysUserService sysUserService;
    private final OssManager ossManager;

    @Override
    @Transactional
    public Long addPost(CommunityPostAddRequest request, List<MultipartFile> imageFiles, SysUser loginUser) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "Post request must not be null");
        ThrowUtils.throwIf(loginUser == null, ErrorCode.NOT_LOGIN_ERROR);
        validatePostContent(request);
        CommunityTag tag = communityTagService.getById(request.getTagId());
        ThrowUtils.throwIf(tag == null || !Integer.valueOf(1).equals(tag.getStatus()),
                ErrorCode.PARAMS_ERROR, "Tag is invalid");

        int imageCount = imageFiles == null ? 0 : (int) imageFiles.stream()
                .filter(file -> file != null && !file.isEmpty())
                .count();
        ThrowUtils.throwIf(imageCount > MAX_IMAGE_COUNT, ErrorCode.PARAMS_ERROR, "Too many images");

        CommunityPost post = new CommunityPost();
        BeanUtil.copyProperties(request, post);
        post.setUserId(loginUser.getId());
        post.setStatus(CommunityPostStatusEnum.PENDING.getValue());
        post.setLikeCount(0);
        post.setCommentCount(0);
        post.setImageCount(imageCount);
        post.setIsPinned(0);
        boolean saved = this.save(post);
        ThrowUtils.throwIf(!saved, ErrorCode.OPERATION_ERROR, "Failed to create post");

        if (imageCount > 0) {
            List<CommunityPostImage> images = new ArrayList<>();
            int sortOrder = 0;
            for (MultipartFile imageFile : imageFiles) {
                if (imageFile == null || imageFile.isEmpty()) {
                    continue;
                }
                String imageUrl = ossManager.uploadCommunityImage(imageFile);
                images.add(CommunityPostImage.builder()
                        .postId(post.getId())
                        .imageUrl(imageUrl)
                        .sortOrder(sortOrder++)
                        .build());
            }
            communityPostImageService.saveBatch(images);
        }
        return post.getId();
    }

    @Override
    public CursorPage<CommunityPostVO> listPostVOByCursor(CommunityPostQueryRequest request, SysUser loginUserOrNull) {
        if (request == null) {
            request = new CommunityPostQueryRequest();
        }
        int pageSize = normalizePageSize(request.getPageSize());
        CommunitySortTypeEnum sortType = CommunitySortTypeEnum.getEnumByValue(request.getSortType());
        ThrowUtils.throwIf(sortType == null, ErrorCode.PARAMS_ERROR, "Unsupported sortType");

        List<CommunityPost> pinnedPosts = new ArrayList<>();
        if (StrUtil.isBlank(request.getCursor())) {
            pinnedPosts = listPinnedPosts(request, pageSize);
        }
        QueryWrapper queryWrapper = buildSquareQuery(request, sortType);
        return executeCursorQuery(queryWrapper, sortType, request.getCursor(), pageSize, loginUserOrNull, pinnedPosts);
    }

    @Override
    public CursorPage<CommunityPostVO> listMyPostVOByCursor(CommunityPostQueryRequest request, SysUser loginUser) {
        ThrowUtils.throwIf(loginUser == null, ErrorCode.NOT_LOGIN_ERROR);
        if (request == null) {
            request = new CommunityPostQueryRequest();
        }
        int pageSize = normalizePageSize(request.getPageSize());
        CommunitySortTypeEnum sortType = CommunitySortTypeEnum.getEnumByValue(request.getSortType());
        ThrowUtils.throwIf(sortType == null, ErrorCode.PARAMS_ERROR, "Unsupported sortType");
        QueryWrapper queryWrapper = buildMyPostQuery(request, loginUser.getId());
        return executeCursorQuery(queryWrapper, sortType, request.getCursor(), pageSize, loginUser, List.of());
    }

    @Override
    public CursorPage<CommunityPostVO> listUserPostVOByCursor(Long userId, CommunityPostQueryRequest request, SysUser loginUserOrNull) {
        ThrowUtils.throwIf(userId == null || userId <= 0, ErrorCode.PARAMS_ERROR, "Invalid userId");
        if (request == null) {
            request = new CommunityPostQueryRequest();
        }
        int pageSize = normalizePageSize(request.getPageSize());
        CommunitySortTypeEnum sortType = CommunitySortTypeEnum.getEnumByValue(request.getSortType());
        ThrowUtils.throwIf(sortType == null, ErrorCode.PARAMS_ERROR, "Unsupported sortType");

        List<CommunityPost> pinnedPosts = new ArrayList<>();
        if (StrUtil.isBlank(request.getCursor())) {
            pinnedPosts = listPinnedPostsByUser(request, userId, pageSize);
        }
        QueryWrapper queryWrapper = buildUserApprovedQuery(request, userId).eq("isPinned", 0);
        return executeCursorQuery(queryWrapper, sortType, request.getCursor(), pageSize, loginUserOrNull, pinnedPosts);
    }

    @Override
    public CommunityPostVO getPostVOById(Long postId, SysUser loginUserOrNull) {
        ThrowUtils.throwIf(postId == null || postId <= 0, ErrorCode.PARAMS_ERROR, "Invalid postId");
        CommunityPost post = this.getById(postId);
        ThrowUtils.throwIf(post == null, ErrorCode.NOT_FOUND_ERROR, "Post not found");
        if (!CommunityPostStatusEnum.APPROVED.getValue().equals(post.getStatus())) {
            boolean canView = loginUserOrNull != null
                    && (post.getUserId().equals(loginUserOrNull.getId())
                    || UserConstant.ADMIN_ROLE.equals(loginUserOrNull.getUserRole()));
            ThrowUtils.throwIf(!canView, ErrorCode.NO_AUTH_ERROR, "No permission");
        }
        return getPostVO(post, loginUserOrNull);
    }

    @Override
    public Page<CommunityPostVO> listPostVOByPageForAdmin(CommunityPostAdminQueryRequest request, SysUser adminUser) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "Request must not be null");
        int pageNum = request.getPageNum() <= 0 ? 1 : request.getPageNum();
        int pageSize = normalizeAdminPageSize(request.getPageSize());
        QueryWrapper queryWrapper = buildAdminPostQuery(request);
        Page<CommunityPost> postPage = this.page(Page.of(pageNum, pageSize), queryWrapper);
        Page<CommunityPostVO> voPage = new Page<>(postPage.getPageNumber(), postPage.getPageSize(), postPage.getTotalRow());
        voPage.setRecords(getPostVOList(postPage.getRecords(), adminUser));
        return voPage;
    }

    @Override
    @Transactional
    public CommunityLikeResultVO togglePostLike(Long postId, SysUser loginUser) {
        ThrowUtils.throwIf(postId == null || postId <= 0, ErrorCode.PARAMS_ERROR, "Invalid postId");
        ThrowUtils.throwIf(loginUser == null, ErrorCode.NOT_LOGIN_ERROR);
        CommunityPost post = this.getById(postId);
        ThrowUtils.throwIf(post == null, ErrorCode.NOT_FOUND_ERROR, "Post not found");
        ThrowUtils.throwIf(!CommunityPostStatusEnum.APPROVED.getValue().equals(post.getStatus()),
                ErrorCode.NO_AUTH_ERROR, "Only approved posts can be liked");

        QueryWrapper likeQuery = QueryWrapper.create()
                .eq("postId", postId)
                .eq("userId", loginUser.getId());
        CommunityPostLike oldLike = communityPostLikeService.getOne(likeQuery);
        boolean liked;
        int likeCount = Optional.ofNullable(post.getLikeCount()).orElse(0);
        if (oldLike == null) {
            CommunityPostLike deletedLike = communityPostLikeService.getOneIncludingDeleted(postId, loginUser.getId());
            if (deletedLike != null) {
                communityPostLikeService.restoreLikeById(deletedLike.getId());
            } else {
                communityPostLikeService.save(CommunityPostLike.builder().postId(postId).userId(loginUser.getId()).build());
            }
            liked = true;
            likeCount++;
        } else {
            communityPostLikeService.removeById(oldLike.getId());
            liked = false;
            likeCount = Math.max(0, likeCount - 1);
        }
        CommunityPost updatePost = new CommunityPost();
        updatePost.setId(postId);
        updatePost.setLikeCount(likeCount);
        this.updateById(updatePost);
        return new CommunityLikeResultVO(liked, likeCount);
    }

    @Override
    public Boolean reviewPost(CommunityPostReviewRequest request, SysUser adminUser) {
        ThrowUtils.throwIf(request == null || request.getPostId() == null, ErrorCode.PARAMS_ERROR, "Invalid review request");
        ThrowUtils.throwIf(adminUser == null, ErrorCode.NOT_LOGIN_ERROR);
        CommunityPostStatusEnum statusEnum = CommunityPostStatusEnum.getEnumByValue(request.getStatus());
        ThrowUtils.throwIf(statusEnum == null || CommunityPostStatusEnum.PENDING.equals(statusEnum),
                ErrorCode.PARAMS_ERROR, "Status must be APPROVED or REJECTED");

        CommunityPost oldPost = this.getById(request.getPostId());
        ThrowUtils.throwIf(oldPost == null, ErrorCode.NOT_FOUND_ERROR, "Post not found");

        CommunityPost updatePost = new CommunityPost();
        updatePost.setId(request.getPostId());
        updatePost.setStatus(statusEnum.getValue());
        updatePost.setReviewerId(adminUser.getId());
        updatePost.setReviewTime(LocalDateTime.now());
        updatePost.setRejectReason(CommunityPostStatusEnum.REJECTED.equals(statusEnum) ? request.getRejectReason() : null);
        if (CommunityPostStatusEnum.REJECTED.equals(statusEnum)) {
            updatePost.setIsPinned(0);
            updatePost.setPinnedTime(null);
        }
        return this.updateById(updatePost);
    }

    @Override
    public Boolean pinPost(CommunityPostPinRequest request, SysUser adminUser) {
        ThrowUtils.throwIf(request == null || request.getPostId() == null, ErrorCode.PARAMS_ERROR, "Invalid pin request");
        ThrowUtils.throwIf(adminUser == null, ErrorCode.NOT_LOGIN_ERROR);
        CommunityPost post = this.getById(request.getPostId());
        ThrowUtils.throwIf(post == null, ErrorCode.NOT_FOUND_ERROR, "Post not found");
        ThrowUtils.throwIf(!post.getUserId().equals(adminUser.getId()), ErrorCode.NO_AUTH_ERROR, "No permission");
        ThrowUtils.throwIf(!CommunityPostStatusEnum.APPROVED.getValue().equals(post.getStatus()),
                ErrorCode.PARAMS_ERROR, "Only approved posts can be pinned");

        CommunityPost updatePost = new CommunityPost();
        updatePost.setId(post.getId());
        boolean pinned = Boolean.TRUE.equals(request.getPinned());
        updatePost.setIsPinned(pinned ? 1 : 0);
        updatePost.setPinnedTime(pinned ? LocalDateTime.now() : null);
        return this.updateById(updatePost);
    }

    @Override
    public List<CommunityPostVO> getPostVOList(List<CommunityPost> posts, SysUser loginUserOrNull) {
        if (CollUtil.isEmpty(posts)) {
            return new ArrayList<>();
        }
        Set<Long> tagIds = posts.stream().map(CommunityPost::getTagId).filter(Objects::nonNull).collect(Collectors.toSet());
        Set<Long> userIds = posts.stream().map(CommunityPost::getUserId).filter(Objects::nonNull).collect(Collectors.toSet());
        Set<Long> postIds = posts.stream().map(CommunityPost::getId).collect(Collectors.toSet());

        Map<Long, CommunityTagVO> tagMap = tagIds.isEmpty() ? new HashMap<>() : communityTagService.listByIds(tagIds)
                .stream()
                .collect(Collectors.toMap(CommunityTag::getId, communityTagService::getTagVO));
        Map<Long, SysUserVO> userMap = userIds.isEmpty() ? new HashMap<>() : sysUserService.listByIds(userIds)
                .stream()
                .collect(Collectors.toMap(SysUser::getId, sysUserService::getSysUserVO));
        Map<Long, List<CommunityPostImageVO>> imageMap = listImageVOMap(postIds);
        Set<Long> likedPostIds = listLikedPostIds(postIds, loginUserOrNull);

        return posts.stream().map(post -> {
            CommunityPostVO vo = copyPostVO(post);
            vo.setTag(tagMap.get(post.getTagId()));
            vo.setUser(userMap.get(post.getUserId()));
            vo.setImages(imageMap.getOrDefault(post.getId(), new ArrayList<>()));
            vo.setLiked(likedPostIds.contains(post.getId()));
            return vo;
        }).toList();
    }

    @Override
    public CommunityPostVO getPostVO(CommunityPost post, SysUser loginUserOrNull) {
        if (post == null) {
            return null;
        }
        return getPostVOList(List.of(post), loginUserOrNull).get(0);
    }

    private void validatePostContent(CommunityPostAddRequest request) {
        ThrowUtils.throwIf(StrUtil.isBlank(request.getTitle()), ErrorCode.PARAMS_ERROR, "Title must not be blank");
        ThrowUtils.throwIf(request.getTitle().length() > 100, ErrorCode.PARAMS_ERROR, "Title too long");
        ThrowUtils.throwIf(StrUtil.isBlank(request.getContent()), ErrorCode.PARAMS_ERROR, "Content must not be blank");
        ThrowUtils.throwIf(request.getContent().length() > 10000, ErrorCode.PARAMS_ERROR, "Content too long");
        ThrowUtils.throwIf(request.getTagId() == null || request.getTagId() <= 0, ErrorCode.PARAMS_ERROR, "tagId is required");
    }

    private int normalizePageSize(Integer pageSize) {
        int normalized = pageSize == null ? 10 : pageSize;
        ThrowUtils.throwIf(normalized <= 0 || normalized > MAX_PAGE_SIZE, ErrorCode.PARAMS_ERROR, "pageSize must be between 1 and 20");
        return normalized;
    }

    private CursorPage<CommunityPostVO> executeCursorQuery(QueryWrapper queryWrapper,
                                                           CommunitySortTypeEnum sortType,
                                                           String cursor,
                                                           int pageSize,
                                                           SysUser loginUserOrNull,
                                                           List<CommunityPost> pinnedPosts) {
        applyCursorAndSort(queryWrapper, sortType, cursor);
        Page<CommunityPost> page = this.page(Page.of(1, pageSize + 1), queryWrapper);
        List<CommunityPost> normalPosts = page.getRecords();
        boolean hasMore = normalPosts.size() > pageSize;
        if (hasMore) {
            normalPosts = normalPosts.subList(0, pageSize);
        }

        List<CommunityPost> mergedPosts = new ArrayList<>(pinnedPosts);
        mergedPosts.addAll(normalPosts);
        CursorPage<CommunityPostVO> cursorPage = new CursorPage<>();
        cursorPage.setRecords(getPostVOList(mergedPosts, loginUserOrNull));
        cursorPage.setHasMore(hasMore);
        cursorPage.setNextCursor(hasMore ? buildNextCursor(sortType, normalPosts.get(normalPosts.size() - 1)) : null);
        return cursorPage;
    }

    private List<CommunityPost> listPinnedPosts(CommunityPostQueryRequest request, int pageSize) {
        QueryWrapper queryWrapper = buildBaseApprovedQuery(request)
                .eq("isPinned", 1)
                .orderBy("pinnedTime", false)
                .orderBy("id", false);
        return this.page(Page.of(1, pageSize), queryWrapper).getRecords();
    }

    private List<CommunityPost> listPinnedPostsByUser(CommunityPostQueryRequest request, Long userId, int pageSize) {
        QueryWrapper queryWrapper = buildUserApprovedQuery(request, userId)
                .eq("isPinned", 1)
                .orderBy("pinnedTime", false)
                .orderBy("id", false);
        return this.page(Page.of(1, pageSize), queryWrapper).getRecords();
    }

    private QueryWrapper buildSquareQuery(CommunityPostQueryRequest request, CommunitySortTypeEnum sortType) {
        QueryWrapper queryWrapper = buildBaseApprovedQuery(request).eq("isPinned", 0);
        applyCursorAndSort(queryWrapper, sortType, request.getCursor());
        return queryWrapper;
    }

    private QueryWrapper buildBaseApprovedQuery(CommunityPostQueryRequest request) {
        QueryWrapper queryWrapper = QueryWrapper.create()
                .eq("status", CommunityPostStatusEnum.APPROVED.getValue());
        if (request.getTagId() != null && request.getTagId() > 0) {
            queryWrapper.eq("tagId", request.getTagId());
        }
        if (StrUtil.isNotBlank(request.getKeyword())) {
            String keyword = "%" + request.getKeyword().trim() + "%";
            queryWrapper.and("(title like ? or content like ?)", keyword, keyword);
        }
        return queryWrapper;
    }

    private QueryWrapper buildMyPostQuery(CommunityPostQueryRequest request, Long userId) {
        QueryWrapper queryWrapper = QueryWrapper.create().eq("userId", userId);
        if (request.getTagId() != null && request.getTagId() > 0) {
            queryWrapper.eq("tagId", request.getTagId());
        }
        if (StrUtil.isNotBlank(request.getKeyword())) {
            String keyword = "%" + request.getKeyword().trim() + "%";
            queryWrapper.and("(title like ? or content like ?)", keyword, keyword);
        }
        if (StrUtil.isNotBlank(request.getStatus())) {
            CommunityPostStatusEnum statusEnum = CommunityPostStatusEnum.getEnumByValue(request.getStatus());
            ThrowUtils.throwIf(statusEnum == null, ErrorCode.PARAMS_ERROR, "Unsupported post status");
            queryWrapper.eq("status", statusEnum.getValue());
        }
        return queryWrapper;
    }

    private QueryWrapper buildUserApprovedQuery(CommunityPostQueryRequest request, Long userId) {
        return buildBaseApprovedQuery(request).eq("userId", userId);
    }

    private void applyCursorAndSort(QueryWrapper queryWrapper, CommunitySortTypeEnum sortType, String cursorValue) {
        CommunityCursorUtils.CursorPayload cursor = CommunityCursorUtils.decodeCursor(cursorValue);
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
            return;
        }
        queryWrapper.orderBy("createTime", false).orderBy("id", false);
    }

    private QueryWrapper buildAdminPostQuery(CommunityPostAdminQueryRequest request) {
        QueryWrapper queryWrapper = QueryWrapper.create();
        if (request.getTagId() != null && request.getTagId() > 0) {
            queryWrapper.eq("tagId", request.getTagId());
        }
        if (StrUtil.isNotBlank(request.getKeyword())) {
            String keyword = "%" + request.getKeyword().trim() + "%";
            queryWrapper.and("(title like ? or content like ?)", keyword, keyword);
        }
        if (StrUtil.isNotBlank(request.getStatus())) {
            CommunityPostStatusEnum statusEnum = CommunityPostStatusEnum.getEnumByValue(request.getStatus());
            ThrowUtils.throwIf(statusEnum == null, ErrorCode.PARAMS_ERROR, "Unsupported post status");
            queryWrapper.eq("status", statusEnum.getValue());
        }
        applyAdminPostSort(queryWrapper, request);
        return queryWrapper;
    }

    private int normalizeAdminPageSize(int pageSize) {
        int normalized = pageSize <= 0 ? 10 : pageSize;
        ThrowUtils.throwIf(normalized > MAX_ADMIN_PAGE_SIZE, ErrorCode.PARAMS_ERROR, "pageSize must be <= 50");
        return normalized;
    }

    private void applyAdminPostSort(QueryWrapper queryWrapper, CommunityPostAdminQueryRequest request) {
        String sortField = request.getSortField();
        if (StrUtil.isNotBlank(sortField) && ADMIN_SORT_FIELDS.contains(sortField)) {
            queryWrapper.orderBy(sortField, "ascend".equalsIgnoreCase(request.getSortOrder()));
            queryWrapper.orderBy("id", false);
            return;
        }
        CommunitySortTypeEnum sortType = CommunitySortTypeEnum.getEnumByValue(request.getSortType());
        ThrowUtils.throwIf(sortType == null, ErrorCode.PARAMS_ERROR, "Unsupported sortType");
        if (CommunitySortTypeEnum.HOT.equals(sortType)) {
            queryWrapper.orderBy("likeCount", false).orderBy("createTime", false).orderBy("id", false);
            return;
        }
        queryWrapper.orderBy("createTime", false).orderBy("id", false);
    }

    private String buildNextCursor(CommunitySortTypeEnum sortType, CommunityPost lastPost) {
        if (CommunitySortTypeEnum.HOT.equals(sortType)) {
            return CommunityCursorUtils.encodeHotCursor(lastPost.getLikeCount(), lastPost.getCreateTime(), lastPost.getId());
        }
        return CommunityCursorUtils.encodeLatestCursor(lastPost.getCreateTime(), lastPost.getId());
    }

    private Map<Long, List<CommunityPostImageVO>> listImageVOMap(Set<Long> postIds) {
        if (postIds.isEmpty()) {
            return new HashMap<>();
        }
        QueryWrapper queryWrapper = QueryWrapper.create()
                .in("postId", postIds)
                .orderBy("sortOrder", true);
        return communityPostImageService.list(queryWrapper).stream()
                .map(image -> {
                    CommunityPostImageVO vo = new CommunityPostImageVO();
                    BeanUtil.copyProperties(image, vo);
                    return Map.entry(image.getPostId(), vo);
                })
                .collect(Collectors.groupingBy(Map.Entry::getKey,
                        Collectors.mapping(Map.Entry::getValue, Collectors.toList())));
    }

    private Set<Long> listLikedPostIds(Set<Long> postIds, SysUser loginUserOrNull) {
        if (loginUserOrNull == null || postIds.isEmpty()) {
            return new HashSet<>();
        }
        QueryWrapper queryWrapper = QueryWrapper.create()
                .in("postId", postIds)
                .eq("userId", loginUserOrNull.getId());
        return communityPostLikeService.list(queryWrapper).stream()
                .map(CommunityPostLike::getPostId)
                .collect(Collectors.toSet());
    }

    private CommunityPostVO copyPostVO(CommunityPost post) {
        CommunityPostVO vo = new CommunityPostVO();
        BeanUtil.copyProperties(post, vo);
        return vo;
    }
}
