package com.yupi.yuaicodemother.ratelimit.enums;


/**
 * 支持用户 ip 接口多个维度的限流
 */
public enum RateLimitType {
    
    /**
     * 接口级别限流
     */
    API,
    
    /**
     * 用户级别限流
     */
    USER,
    
    /**
     * IP级别限流
     */
    IP
}
