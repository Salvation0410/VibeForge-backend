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
import com.yupi.yuaicodemother.utils.ScreenshotTimings;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.beans.factory.annotation.Value;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.UUID;

/**
 * 网页截图服务实现
 */
@Service
@Slf4j
@RequiredArgsConstructor
public class ScreenshotServiceImpl implements ScreenshotService {

    private static final DateTimeFormatter SCREENSHOT_DATE_FORMATTER = DateTimeFormatter.ofPattern("yyyy/MM/dd");

    private final OssProperties ossProperties;

    @Value("${app.screenshot.driver-path:}")
    private String driverPath;

    /**
     * 生成网页截图并上传至 OSS
     * @param webUrl 网页地址
     * @return
     */
    @Override
    public String generateAndUploadScreenshot(String webUrl) {
        ThrowUtils.throwIf(StrUtil.isBlank(webUrl), ErrorCode.PARAMS_ERROR, "网页URL不能为空");
        String screenshotId = ScreenshotTimings.newId();
        String localScreenshotPath = WebScreenshotUtils.saveWebPageScreenshot(webUrl, driverPath, screenshotId);
        ThrowUtils.throwIf(StrUtil.isBlank(localScreenshotPath), ErrorCode.OPERATION_ERROR, "本地截图生成失败");

        try {
            return ScreenshotTimings.measure(screenshotId, ScreenshotTimings.Stage.UPLOAD, () -> {
                String ossUrl = uploadScreenshotToOss(localScreenshotPath);
                ThrowUtils.throwIf(StrUtil.isBlank(ossUrl), ErrorCode.OPERATION_ERROR, "截图上传 OSS 失败");
                return ossUrl;
            });
        } finally {
            try {
                ScreenshotTimings.measure(screenshotId, ScreenshotTimings.Stage.CLEANUP, () -> {
                    cleanupLocalFile(localScreenshotPath);
                    return null;
                });
            } catch (RuntimeException ignored) {
                // 清理错误已有固定阶段日志，不能遮盖上传的原始结果。
            }
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
     * 同日多次截图可能使用相同本地文件名，随机前缀防止覆盖其他应用封面。
     */
    private String generateScreenshotObjectKey(String fileName) {
        String datePath = LocalDate.now().format(SCREENSHOT_DATE_FORMATTER);
        return String.format("screenshots/%s/%s-%s", datePath, UUID.randomUUID(), fileName);
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
