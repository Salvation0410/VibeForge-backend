package com.yupi.yuaicodemother.model.vo;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

@Data
public class CommunityPostImageVO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private Long id;

    private String imageUrl;

    private Integer sortOrder;
}
