package com.yupi.yuaicodemother.enums;

import lombok.Getter;

/**
 * 社区评论的审核状态。
 */
@Getter
public enum CommunityCommentStatusEnum {

    APPROVED("Approved", "APPROVED"),
    REJECTED("Rejected", "REJECTED");

    private final String text;

    private final String value;

    CommunityCommentStatusEnum(String text, String value) {
        this.text = text;
        this.value = value;
    }

    public static CommunityCommentStatusEnum getEnumByValue(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        for (CommunityCommentStatusEnum item : values()) {
            if (item.value.equals(value)) {
                return item;
            }
        }
        return null;
    }
}
