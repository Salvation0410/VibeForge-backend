package com.yupi.yuaicodemother.common;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;
import java.util.List;

/**
 * 为需要稳定无限滚动的列表提供基于游标的分页结果。
 */
@Data
public class CursorPage<T> implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    /**
     * 当前页记录。
     */
    private List<T> records;

    /**
     * 下一页游标；为空表示没有下一页。
     */
    private String nextCursor;

    /**
     * 客户端是否可以使用 nextCursor 请求下一页。
     */
    private Boolean hasMore;
}
