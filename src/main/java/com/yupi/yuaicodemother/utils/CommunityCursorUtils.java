package com.yupi.yuaicodemother.utils;

import cn.hutool.core.util.StrUtil;
import cn.hutool.json.JSONUtil;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import lombok.Data;

import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.Base64;

/**
 * Encodes feed cursor fields into an opaque string for frontend pagination.
 */
public final class CommunityCursorUtils {

    private CommunityCursorUtils() {
    }

    public static String encodeLatestCursor(LocalDateTime lastCreateTime, Long lastId) {
        CursorPayload payload = new CursorPayload();
        payload.setLastCreateTime(lastCreateTime);
        payload.setLastId(lastId);
        return encode(payload);
    }

    public static String encodeHotCursor(Integer lastLikeCount, LocalDateTime lastCreateTime, Long lastId) {
        CursorPayload payload = new CursorPayload();
        payload.setLastLikeCount(lastLikeCount);
        payload.setLastCreateTime(lastCreateTime);
        payload.setLastId(lastId);
        return encode(payload);
    }

    public static CursorPayload decodeCursor(String cursor) {
        if (StrUtil.isBlank(cursor)) {
            return new CursorPayload();
        }
        try {
            byte[] bytes = Base64.getUrlDecoder().decode(cursor);
            String json = new String(bytes, StandardCharsets.UTF_8);
            return JSONUtil.toBean(json, CursorPayload.class);
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.PARAMS_ERROR, "游标格式错误");
        }
    }

    private static String encode(CursorPayload payload) {
        String json = JSONUtil.toJsonStr(payload);
        return Base64.getUrlEncoder().withoutPadding().encodeToString(json.getBytes(StandardCharsets.UTF_8));
    }

    @Data
    public static class CursorPayload {

        private Integer lastLikeCount;

        private LocalDateTime lastCreateTime;

        private Long lastId;
    }
}
