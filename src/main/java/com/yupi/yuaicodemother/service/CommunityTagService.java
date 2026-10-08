package com.yupi.yuaicodemother.service;

import com.mybatisflex.core.service.IService;
import com.yupi.yuaicodemother.model.dto.community.CommunityTagSaveRequest;
import com.yupi.yuaicodemother.model.entity.CommunityTag;
import com.yupi.yuaicodemother.model.vo.CommunityTagVO;

import java.util.List;

public interface CommunityTagService extends IService<CommunityTag> {

    /**
     * 查询供公开发帖和筛选使用的启用标签。
     */
    List<CommunityTagVO> listEnabledTags();

    /**
     * 查询供管理员管理的全部标签。
     */
    List<CommunityTagVO> listAllTags();

    /**
     * 在管理端创建或更新标签。
     */
    Boolean saveTag(CommunityTagSaveRequest request);

    /**
     * 将标签实体转换为前端安全视图对象。
     */
    CommunityTagVO getTagVO(CommunityTag tag);
}
