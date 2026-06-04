package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.entity.CommunityPostLike;

public interface CommunityPostLikeService extends IService<CommunityPostLike> {

    CommunityPostLike getOneIncludingDeleted(Long postId, Long userId);

    boolean restoreLikeById(Long id);
}
