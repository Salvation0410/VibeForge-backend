package com.yupi.yuaicodemother.common;

import lombok.Data;

import java.io.Serial;
import java.io.Serializable;
import java.util.List;

/**
 * Cursor-based page result for feeds that need stable infinite scrolling.
 */
@Data
public class CursorPage<T> implements Serializable {

    @Serial
    private static final long serialVersionUID = 1L;

    /**
     * Current page records.
     */
    private List<T> records;

    /**
     * Cursor for the next page. Blank means there is no next page.
     */
    private String nextCursor;

    /**
     * Whether the client can request another page with nextCursor.
     */
    private Boolean hasMore;
}
