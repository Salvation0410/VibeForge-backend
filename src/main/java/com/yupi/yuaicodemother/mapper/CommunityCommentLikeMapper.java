package com.yupi.yuaicodemother.mapper;

import com.mybatisflex.core.BaseMapper;
import com.yupi.yuaicodemother.model.entity.CommunityCommentLike;
import org.apache.ibatis.annotations.Param;

public interface CommunityCommentLikeMapper extends BaseMapper<CommunityCommentLike> {

    CommunityCommentLike selectOneIncludingDeleted(@Param("commentId") Long commentId, @Param("userId") Long userId);

    boolean restoreLikeById(@Param("id") Long id);
}
