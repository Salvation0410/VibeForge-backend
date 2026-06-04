package com.yupi.yuaicodemother.model.dto.community;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

@Data
public class CommunityPostAddRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private String title;

    private String content;

    private Long tagId;
}
