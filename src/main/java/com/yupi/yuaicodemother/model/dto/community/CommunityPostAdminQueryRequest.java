package com.yupi.yuaicodemother.model.dto.community;

import com.yupi.yuaicodemother.common.PageRequest;
import lombok.Data;
import lombok.EqualsAndHashCode;

import java.io.Serial;

@Data
@EqualsAndHashCode(callSuper = true)
public class CommunityPostAdminQueryRequest extends PageRequest {

    @Serial
    private static final long serialVersionUID = 1L;

    private Long tagId;

    private String keyword;

    private String status;

    private String sortType = "latest";
}
