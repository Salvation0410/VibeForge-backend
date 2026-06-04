package com.yupi.yuaicodemother.service.impl;

import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.mapper.CommunityPostImageMapper;
import com.yupi.yuaicodemother.model.entity.CommunityPostImage;
import com.yupi.yuaicodemother.service.CommunityPostImageService;
import org.springframework.stereotype.Service;

@Service
public class CommunityPostImageServiceImpl extends ServiceImpl<CommunityPostImageMapper, CommunityPostImage>
        implements CommunityPostImageService {
}
