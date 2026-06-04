package com.yupi.yuaicodemother.mapper;

import com.mybatisflex.core.BaseMapper;
import com.yupi.yuaicodemother.model.entity.CommunityPostLike;
import org.apache.ibatis.annotations.Param;

public interface CommunityPostLikeMapper extends BaseMapper<CommunityPostLike> {

    CommunityPostLike selectOneIncludingDeleted(@Param("postId") Long postId, @Param("userId") Long userId);

    boolean restoreLikeById(@Param("id") Long id);
}
