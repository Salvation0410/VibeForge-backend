package com.yupi.yuaicodemother.service.impl;

import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.mapper.CommunityCommentLikeMapper;
import com.yupi.yuaicodemother.model.entity.CommunityCommentLike;
import com.yupi.yuaicodemother.service.CommunityCommentLikeService;
import org.springframework.stereotype.Service;

@Service
public class CommunityCommentLikeServiceImpl extends ServiceImpl<CommunityCommentLikeMapper, CommunityCommentLike>
        implements CommunityCommentLikeService {

    @Override
    public CommunityCommentLike getOneIncludingDeleted(Long commentId, Long userId) {
        return mapper.selectOneIncludingDeleted(commentId, userId);
    }

    @Override
    public boolean restoreLikeById(Long id) {
        return mapper.restoreLikeById(id);
    }
}
