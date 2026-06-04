package com.yupi.yuaicodemother.model.dto.community;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

@Data
public class CommunityPostQueryRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private Integer pageSize = 10;

    private String cursor;

    private String keyword;

    private Long tagId;

    private String status;

    private String sortType = "latest";
}
