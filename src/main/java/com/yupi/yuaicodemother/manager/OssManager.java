package com.yupi.yuaicodemother.manager;

import cn.hutool.core.date.DateUtil;
import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import com.aliyun.oss.OSS;
import com.aliyun.oss.OSSClientBuilder;
import com.aliyun.oss.model.ObjectMetadata;
import com.aliyun.oss.model.PutObjectRequest;
import com.yupi.yuaicodemother.config.OssProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.io.InputStream;
import java.util.Set;

/**
 * OSS 管理器
 */
@Component
@RequiredArgsConstructor
public class OssManager {

    private static final Set<String> ALLOWED_IMAGE_EXTENSIONS = Set.of("jpg", "jpeg", "png", "gif", "webp", "bmp");

    private final OssProperties ossProperties;

    /**
     * 上传头像
     *
     * @param avatarFile 头像文件
     * @return 头像地址
     */
    public String uploadAvatar(MultipartFile avatarFile) {
        validateAvatarFile(avatarFile);
        validateOssConfig();

        String objectKey = buildAvatarObjectKey(avatarFile.getOriginalFilename());
        String endpoint = normalizeEndpoint(ossProperties.getEndpoint());

        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(avatarFile.getSize());
        metadata.setContentType(StrUtil.blankToDefault(avatarFile.getContentType(), "application/octet-stream"));

        try (InputStream inputStream = avatarFile.getInputStream()) {
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
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "头像文件读取失败");
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "头像上传失败");
        }
    }

    private void validateAvatarFile(MultipartFile avatarFile) {
        ThrowUtils.throwIf(avatarFile == null || avatarFile.isEmpty(), ErrorCode.PARAMS_ERROR, "头像文件不能为空");
        ThrowUtils.throwIf(avatarFile.getSize() > ossProperties.getMaxAvatarSize(), ErrorCode.PARAMS_ERROR, "头像文件大小不能超过 5MB");

        String contentType = avatarFile.getContentType();
        ThrowUtils.throwIf(StrUtil.isBlank(contentType) || !contentType.startsWith("image/"), ErrorCode.PARAMS_ERROR, "仅支持上传图片文件");

        String extension = FileUtil.extName(avatarFile.getOriginalFilename());
        ThrowUtils.throwIf(StrUtil.isBlank(extension) || !ALLOWED_IMAGE_EXTENSIONS.contains(extension.toLowerCase()), ErrorCode.PARAMS_ERROR, "头像文件格式不支持");
    }

    private void validateOssConfig() {
        ThrowUtils.throwIf(StrUtil.hasBlank(
                ossProperties.getBucketName(),
                ossProperties.getAccessKeyId(),
                ossProperties.getAccessKeySecret(),
                ossProperties.getEndpoint()
        ), ErrorCode.SYSTEM_ERROR, "OSS 配置不完整");
    }

    private String buildAvatarObjectKey(String originalFilename) {
        String extension = FileUtil.extName(originalFilename);
        if (StrUtil.isBlank(extension)) {
            extension = "png";
        }
        String avatarDir = StrUtil.blankToDefault(ossProperties.getAvatarDir(), "user-avatar");
        avatarDir = StrUtil.removeSuffix(avatarDir, "/");
        String datePath = DateUtil.today().replace("-", "/");
        return StrUtil.format("{}/{}/{}.{}", avatarDir, datePath, IdUtil.fastSimpleUUID(), extension.toLowerCase());
    }

    private String buildFileUrl(String objectKey) {
        String website = ossProperties.getWebSite();
        if (StrUtil.isNotBlank(website)) {
            return StrUtil.addSuffixIfNot(website, "/") + objectKey;
        }
        String endpoint = normalizeEndpoint(ossProperties.getEndpoint());
        return StrUtil.format("{}/{}/{}", endpoint, ossProperties.getBucketName(), objectKey);
    }

    private String normalizeEndpoint(String endpoint) {
        if (StrUtil.startWithAnyIgnoreCase(endpoint, "http://", "https://")) {
            return endpoint;
        }
        return "https://" + endpoint;
    }
}
