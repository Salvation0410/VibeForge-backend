package com.yupi.yuaicodemother.enums;

import lombok.Getter;

/**
 * Sort type shared by community post and comment feeds.
 */
@Getter
public enum CommunitySortTypeEnum {

    LATEST("最新", "latest"),
    HOT("最热", "hot");

    private final String text;

    private final String value;

    CommunitySortTypeEnum(String text, String value) {
        this.text = text;
        this.value = value;
    }

    public static CommunitySortTypeEnum getEnumByValue(String value) {
        if (value == null || value.isBlank()) {
            return LATEST;
        }
        for (CommunitySortTypeEnum item : values()) {
            if (item.value.equalsIgnoreCase(value)) {
                return item;
            }
        }
        return null;
    }
}
