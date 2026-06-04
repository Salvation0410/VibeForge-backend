package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.dto.community.CommunityTagSaveRequest;
import com.yupi.yuaicodemother.model.entity.CommunityTag;
import com.yupi.yuaicodemother.model.vo.CommunityTagVO;

import java.util.List;

public interface CommunityTagService extends IService<CommunityTag> {

    /**
     * Lists enabled tags for public post publishing and filtering.
     */
    List<CommunityTagVO> listEnabledTags();

    /**
     * Lists all tags for admin management.
     */
    List<CommunityTagVO> listAllTags();

    /**
     * Creates or updates a tag from the admin console.
     */
    Boolean saveTag(CommunityTagSaveRequest request);

    /**
     * Converts tag entity to frontend-safe view object.
     */
    CommunityTagVO getTagVO(CommunityTag tag);
}
