package com.yupi.yuaicodemother.model.dto.community;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;

@Data
public class CommunityCommentReviewRequest implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private Long commentId;

    private String status;

    private String rejectReason;
}
