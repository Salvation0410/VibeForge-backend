package com.yupi.yuaicodemother.model.vo;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;
import java.time.LocalDateTime;

@Data
public class CommunityCommentVO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private Long id;

    private Long postId;

    private Long userId;

    private Long parentId;

    private Long rootId;

    private Long replyUserId;

    private Integer depth;

    private String path;

    private String content;

    private String status;

    private Integer likeCount;

    private Integer replyCount;

    private Long reviewerId;

    private LocalDateTime reviewTime;

    private String rejectReason;

    private LocalDateTime createTime;

    private SysUserVO user;

    private SysUserVO replyUser;

    private Boolean liked;
}
