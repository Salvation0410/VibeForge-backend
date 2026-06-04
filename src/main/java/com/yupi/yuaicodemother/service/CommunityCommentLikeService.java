package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.entity.CommunityCommentLike;

public interface CommunityCommentLikeService extends IService<CommunityCommentLike> {

    CommunityCommentLike getOneIncludingDeleted(Long commentId, Long userId);

    boolean restoreLikeById(Long id);
}
