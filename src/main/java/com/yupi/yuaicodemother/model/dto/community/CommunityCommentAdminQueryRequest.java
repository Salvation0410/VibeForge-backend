package com.yupi.yuaicodemother.model.dto.community;

import com.yupi.yuaicodemother.common.PageRequest;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.io.Serial;

@Data
@EqualsAndHashCode(callSuper = true)
public class CommunityCommentAdminQueryRequest extends PageRequest {

    @Serial
    private static final long serialVersionUID = 1L;

    private Long postId;

    private Long parentId;

    private Long rootId;

    private Long userId;

    private String keyword;
}
