package com.yupi.yuaicodemother.enums;

import lombok.Getter;

/**
 * Review status for community posts.
 */
@Getter
public enum CommunityPostStatusEnum {

    PENDING("待审核", "PENDING"),
    APPROVED("已通过", "APPROVED"),
    REJECTED("已拒绝", "REJECTED");

    private final String text;

    private final String value;

    CommunityPostStatusEnum(String text, String value) {
        this.text = text;
        this.value = value;
    }

    public static CommunityPostStatusEnum getEnumByValue(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        for (CommunityPostStatusEnum item : values()) {
            if (item.value.equals(value)) {
                return item;
            }
        }
        return null;
    }
}
