package com.yupi.yuaicodemother.config;

import lombok.Data;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.stereotype.Component;

/** HTML 发布前浏览器烟测开关；默认必须完成验证才允许切换活动版本。 */
@Data
@Component
@ConfigurationProperties(prefix = "ai.html-artifact")
public class HtmlArtifactProperties {
    private boolean enabled = true;
    private boolean required = true;
    /** 允许单文件 HTML 继续执行全量重写的当前源码字符数上限。 */
    private int maxRewriteSourceChars = 24_000;
}
