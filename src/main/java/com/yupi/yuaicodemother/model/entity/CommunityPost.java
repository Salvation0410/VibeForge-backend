package com.yupi.yuaicodemother.model.entity;

import com.mybatisflex.annotation.Column;
import com.mybatisflex.annotation.Id;
import com.mybatisflex.annotation.KeyType;
import com.mybatisflex.annotation.Table;
import com.mybatisflex.core.keygen.KeyGenerators;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.io.Serial;
import java.io.Serializable;
import java.time.LocalDateTime;

@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@Table("community_post")
public class CommunityPost implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    @Id(keyType = KeyType.Generator, value = KeyGenerators.snowFlakeId)
    private Long id;

    private String title;

    private String content;

    @Column("tagId")
    private Long tagId;

    @Column("userId")
    private Long userId;

    private String status;

    @Column("likeCount")
    private Integer likeCount;

    @Column("commentCount")
    private Integer commentCount;

    @Column("imageCount")
    private Integer imageCount;

    @Column("isPinned")
    private Integer isPinned;

    @Column("pinnedTime")
    private LocalDateTime pinnedTime;

    @Column("reviewerId")
    private Long reviewerId;

    @Column("reviewTime")
    private LocalDateTime reviewTime;

    @Column("rejectReason")
    private String rejectReason;

    @Column("createTime")
    private LocalDateTime createTime;

    @Column("updateTime")
    private LocalDateTime updateTime;

    @Column(value = "isDelete", isLogicDelete = true)
    private Integer isDelete;
}
