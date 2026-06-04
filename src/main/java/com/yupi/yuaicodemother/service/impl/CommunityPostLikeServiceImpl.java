package com.yupi.yuaicodemother.service.impl;

import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.mapper.CommunityPostLikeMapper;
import com.yupi.yuaicodemother.model.entity.CommunityPostLike;
import com.yupi.yuaicodemother.service.CommunityPostLikeService;
import org.springframework.stereotype.Service;

@Service
public class CommunityPostLikeServiceImpl extends ServiceImpl<CommunityPostLikeMapper, CommunityPostLike>
        implements CommunityPostLikeService {

    @Override
    public CommunityPostLike getOneIncludingDeleted(Long postId, Long userId) {
        return mapper.selectOneIncludingDeleted(postId, userId);
    }

    @Override
    public boolean restoreLikeById(Long id) {
        return mapper.restoreLikeById(id);
    }
}
