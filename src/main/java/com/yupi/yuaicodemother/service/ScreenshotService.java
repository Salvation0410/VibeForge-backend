package com.yupi.yuaicodemother.service;

/**
 * 网页截图服务
 */
public interface ScreenshotService {

    /**
     * 生成网页截图并上传到阿里云 OSS
     *
     * @param webUrl 网页地址
     * @return OSS 访问地址
     */
    String generateAndUploadScreenshot(String webUrl);
}
