package com.yupi.yuaicodemother.model.dto.community;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

@Data
public class CommunityTagSaveRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private Long id;

    private String name;

    private String description;

    private Integer sortOrder;

    private Integer status;
}
