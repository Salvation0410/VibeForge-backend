package com.yupi.yuaicodemother.service.impl;

import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.StrUtil;
import com.aliyun.oss.OSS;
import com.aliyun.oss.OSSClientBuilder;
import com.aliyun.oss.model.ObjectMetadata;
import com.aliyun.oss.model.PutObjectRequest;
import com.yupi.yuaicodemother.config.OssProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import com.yupi.yuaicodemother.service.ScreenshotService;
import com.yupi.yuaicodemother.utils.WebScreenshotUtils;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;

/**
 * 网页截图服务实现
 */
@Service
@Slf4j
@RequiredArgsConstructor
public class ScreenshotServiceImpl implements ScreenshotService {

    private static final DateTimeFormatter SCREENSHOT_DATE_FORMATTER = DateTimeFormatter.ofPattern("yyyy/MM/dd");

    private final OssProperties ossProperties;

    /**
     * 生成网页截图并上传至 OSS
     * @param webUrl 网页地址
     * @return
     */
    @Override
    public String generateAndUploadScreenshot(String webUrl) {
        ThrowUtils.throwIf(StrUtil.isBlank(webUrl), ErrorCode.PARAMS_ERROR, "网页URL不能为空");
        log.info("开始生成网页截图，URL: {}", webUrl);

        String localScreenshotPath = WebScreenshotUtils.saveWebPageScreenshot(webUrl);
        ThrowUtils.throwIf(StrUtil.isBlank(localScreenshotPath), ErrorCode.OPERATION_ERROR, "本地截图生成失败");

        try {
            String ossUrl = uploadScreenshotToOss(localScreenshotPath);
            ThrowUtils.throwIf(StrUtil.isBlank(ossUrl), ErrorCode.OPERATION_ERROR, "截图上传 OSS 失败");
            log.info("网页截图生成并上传成功: {} -> {}", webUrl, ossUrl);
            return ossUrl;
        } finally {
            cleanupLocalFile(localScreenshotPath);
        }
    }

    /**
     * 上传截图文件至 OSS
     * @param localScreenshotPath
     * @return
     */
    private String uploadScreenshotToOss(String localScreenshotPath) {
        if (StrUtil.isBlank(localScreenshotPath)) {
            return null;
        }

        File screenshotFile = new File(localScreenshotPath);
        if (!screenshotFile.exists()) {
            log.error("截图文件不存在: {}", localScreenshotPath);
            return null;
        }

        validateOssConfig();

        String objectKey = generateScreenshotObjectKey(screenshotFile.getName());
        String endpoint = normalizeEndpoint(ossProperties.getEndpoint());

        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(screenshotFile.length());
        metadata.setContentType("image/jpeg");

        try (InputStream inputStream = new FileInputStream(screenshotFile)) {
            OSS ossClient = new OSSClientBuilder().build(
                    endpoint,
                    ossProperties.getAccessKeyId(),
                    ossProperties.getAccessKeySecret()
            );
            try {
                PutObjectRequest putObjectRequest = new PutObjectRequest(
                        ossProperties.getBucketName(),
                        objectKey,
                        inputStream,
                        metadata
                );
                ossClient.putObject(putObjectRequest);
            } finally {
                ossClient.shutdown();
            }
            return buildFileUrl(objectKey);
        } catch (IOException e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "截图文件读取失败");
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "截图上传 OSS 失败");
        }
    }

    /**
     * 生成截图对象存储键
     * 格式：screenshots/2025/07/31/filename.jpg
     */
    private String generateScreenshotObjectKey(String fileName) {
        String datePath = LocalDate.now().format(SCREENSHOT_DATE_FORMATTER);
        return String.format("screenshots/%s/%s", datePath, fileName);
    }

    private void cleanupLocalFile(String localFilePath) {
        if (StrUtil.isBlank(localFilePath)) {
            return;
        }

        File localFile = new File(localFilePath);
        if (!localFile.exists()) {
            return;
        }

        File parentDir = localFile.getParentFile();
        FileUtil.del(localFile);
        if (parentDir != null && parentDir.exists()) {
            FileUtil.del(parentDir);
        }
        log.info("本地截图文件已清理: {}", localFilePath);
    }

    private void validateOssConfig() {
        ThrowUtils.throwIf(StrUtil.hasBlank(
                ossProperties.getBucketName(),
                ossProperties.getAccessKeyId(),
                ossProperties.getAccessKeySecret(),
                ossProperties.getEndpoint()
        ), ErrorCode.SYSTEM_ERROR, "OSS 配置不完整");
    }

    private String buildFileUrl(String objectKey) {
        String website = ossProperties.getWebSite();
        if (StrUtil.isNotBlank(website)) {
            return StrUtil.addSuffixIfNot(website, "/") + StrUtil.removePrefix(objectKey, "/");
        }
        String endpoint = normalizeEndpoint(ossProperties.getEndpoint());
        return StrUtil.format("{}/{}/{}", endpoint, ossProperties.getBucketName(), StrUtil.removePrefix(objectKey, "/"));
    }

    private String normalizeEndpoint(String endpoint) {
        if (StrUtil.startWithAnyIgnoreCase(endpoint, "http://", "https://")) {
            return endpoint;
        }
        return "https://" + endpoint;
    }
}
