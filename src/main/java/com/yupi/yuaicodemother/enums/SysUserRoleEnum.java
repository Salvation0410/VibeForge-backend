package com.yupi.yuaicodemother.enums;

import cn.hutool.core.util.ObjUtil;
import lombok.Getter;

/**
 * @author huang
 * @version 1.0
 * @description 用户角色枚举
 * @date 2026/5/20
 */

@Getter
public enum SysUserRoleEnum {

    USER("用户", "user"),
    ADMIN("管理员", "admin");

    private final String text;

    private final String value;

    SysUserRoleEnum(String text, String value) {
        this.text = text;
        this.value = value;
    }

    /**
     * 根据 value 获取枚举
     *
     * @param value 枚举值的value
     * @return 枚举值
     */
    public static SysUserRoleEnum getEnumByValue(String value) {
        if (ObjUtil.isEmpty(value)) {
            return null;
        }
        for (SysUserRoleEnum anEnum : SysUserRoleEnum.values()) {
            if (anEnum.value.equals(value)) {
                return anEnum;
            }
        }
        return null;
    }
}
