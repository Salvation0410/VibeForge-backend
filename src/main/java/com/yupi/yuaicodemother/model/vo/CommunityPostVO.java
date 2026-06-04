package com.yupi.yuaicodemother.model.vo;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;
import java.time.LocalDateTime;
import java.util.List;

@Data
public class CommunityPostVO implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    private Long id;

    private String title;

    private String content;

    private Long tagId;

    private Long userId;

    private String status;

    private Integer likeCount;

    private Integer commentCount;

    private Integer imageCount;

    private Integer isPinned;

    private LocalDateTime pinnedTime;

    private String rejectReason;

    private LocalDateTime createTime;

    private LocalDateTime updateTime;

    private CommunityTagVO tag;

    private SysUserVO user;

    private List<CommunityPostImageVO> images;

    private Boolean liked;
}
