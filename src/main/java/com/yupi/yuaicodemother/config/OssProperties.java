package com.yupi.yuaicodemother.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/**
 * OSS 配置
 */
@Data
@Component
@ConfigurationProperties(prefix = "oss")
public class OssProperties {

    /**
     * Bucket 名称
     */
    private String bucketName;

    /**
     * AccessKeyId
     */
    private String accessKeyId;

    /**
     * AccessKeySecret
     */
    private String accessKeySecret;

    /**
     * OSS endpoint
     */
    private String endpoint;

    /**
     * 访问域名
     */
    private String webSite;

    /**
     * 头像目录
     */
    private String avatarDir = "user-avatar";

    /**
     * 头像最大大小
     */
    private long maxAvatarSize = 5 * 1024 * 1024;
}
