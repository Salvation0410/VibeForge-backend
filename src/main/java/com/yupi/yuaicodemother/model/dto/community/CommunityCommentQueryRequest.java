package com.yupi.yuaicodemother.model.dto.community;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

@Data
public class CommunityCommentQueryRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private Long postId;

    private Long parentId;

    private Integer pageSize = 10;

    private String cursor;

    private String sortType = "latest";
}
