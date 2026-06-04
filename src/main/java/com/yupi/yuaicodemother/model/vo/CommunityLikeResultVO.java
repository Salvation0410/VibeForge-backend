package com.yupi.yuaicodemother.model.vo;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serial;
import java.io.Serializable;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class CommunityLikeResultVO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private Boolean liked;

    private Integer likeCount;
}
