package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.util.StrUtil;
import com.mybatisflex.core.query.QueryWrapper;
import com.mybatisflex.spring.service.impl.ServiceImpl;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.mapper.CommunityTagMapper;
import com.yupi.yuaicodemother.model.dto.community.CommunityTagSaveRequest;
import com.yupi.yuaicodemother.model.entity.CommunityTag;
import com.yupi.yuaicodemother.model.vo.CommunityTagVO;
import com.yupi.yuaicodemother.service.CommunityTagService;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;

import java.util.List;

@Service
public class CommunityTagServiceImpl extends ServiceImpl<CommunityTagMapper, CommunityTag> implements CommunityTagService {

    @Override
    public List<CommunityTagVO> listEnabledTags() {
        QueryWrapper queryWrapper = QueryWrapper.create()
                .eq("status", 1)
                .orderBy("sortOrder", true)
                .orderBy("createTime", false);
        return this.list(queryWrapper).stream().map(this::getTagVO).toList();
    }

    @Override
    public List<CommunityTagVO> listAllTags() {
        QueryWrapper queryWrapper = QueryWrapper.create()
                .orderBy("sortOrder", true)
                .orderBy("createTime", false);
        return this.list(queryWrapper).stream().map(this::getTagVO).toList();
    }

    @Override
    public Boolean saveTag(CommunityTagSaveRequest request) {
        ThrowUtils.throwIf(request == null, ErrorCode.PARAMS_ERROR, "标签参数不能为空");
        ThrowUtils.throwIf(StrUtil.isBlank(request.getName()), ErrorCode.PARAMS_ERROR, "标签名称不能为空");
        String name = StrUtil.trim(request.getName());
        Long tagId = request.getId();
        validateUniqueName(name, tagId);
        CommunityTag tag = new CommunityTag();
        BeanUtil.copyProperties(request, tag);
        tag.setName(name);
        if (tag.getStatus() == null) {
            tag.setStatus(1);
        }
        if (tag.getSortOrder() == null) {
            tag.setSortOrder(0);
        }
        try {
            return tagId == null ? this.save(tag) : this.updateById(tag);
        } catch (DuplicateKeyException e) {
            throw new BusinessException(ErrorCode.OPERATION_ERROR, "标签名称已存在");
        }
    }

    @Override
    public CommunityTagVO getTagVO(CommunityTag tag) {
        if (tag == null) {
            return null;
        }
        CommunityTagVO vo = new CommunityTagVO();
        BeanUtil.copyProperties(tag, vo);
        return vo;
    }

    private void validateUniqueName(String name, Long tagId) {
        QueryWrapper queryWrapper = QueryWrapper.create().eq("name", name);
        if (tagId != null) {
            queryWrapper.ne("id", tagId);
        }
        Long sameNameCount = this.count(queryWrapper);
        ThrowUtils.throwIf(sameNameCount > 0, ErrorCode.OPERATION_ERROR, "标签名称已存在");
    }
}
