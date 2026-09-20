package com.yupi.yuaicodemother.ai.gateway;

import lombok.Getter;

/** 携带 SSE 终止错误码和请求 ID，避免流开始后丢失业务失败语义。 */
@Getter
public class GenerationStreamException extends RuntimeException {
    private final int code;
    private final String errorCode;
    private final String requestId;

    /** 创建可序列化为 SSE error 事件的生成异常。 */
    public GenerationStreamException(int code, String errorCode, String requestId, String message, Throwable cause) {
        super(message, cause);
        this.code = code; this.errorCode = errorCode; this.requestId = requestId;
    }
}
