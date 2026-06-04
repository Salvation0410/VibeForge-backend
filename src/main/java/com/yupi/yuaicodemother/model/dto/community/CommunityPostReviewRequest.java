package com.yupi.yuaicodemother.model.dto.community;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

@Data
public class CommunityPostReviewRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private Long postId;

    private String status;

    private String rejectReason;
}
