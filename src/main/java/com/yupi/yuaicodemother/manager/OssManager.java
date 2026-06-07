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

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.util.Set;

/**
 * OSS file upload manager.
 */
@Component
@RequiredArgsConstructor
public class OssManager {

    private static final Set<String> ALLOWED_IMAGE_EXTENSIONS = Set.of("jpg", "jpeg", "png", "gif", "webp", "bmp");

    private final OssProperties ossProperties;

    /**
     * Uploads a user avatar and returns the public URL.
     */
    public String uploadAvatar(MultipartFile avatarFile) {
        validateImageFile(avatarFile, ossProperties.getMaxAvatarSize(), "头像");
        validateOssConfig();
        String objectKey = buildImageObjectKey(
                StrUtil.blankToDefault(ossProperties.getAvatarDir(), "user-avatar"),
                avatarFile.getOriginalFilename()
        );
        return uploadImageFile(avatarFile, objectKey, "头像");
    }

    /**
     * Uploads a community post image and returns the public URL.
     */
    public String uploadCommunityImage(MultipartFile imageFile) {
        validateImageFile(imageFile, ossProperties.getMaxAvatarSize(), "社区图片");
        validateOssConfig();
        String objectKey = buildImageObjectKey("community-image", imageFile.getOriginalFilename());
        return uploadImageFile(imageFile, objectKey, "社区图片");
    }

    /**
     * Uploads a local file and returns the public URL.
     */
    public String uploadFile(String objectKey, File file) {
        ThrowUtils.throwIf(StrUtil.isBlank(objectKey), ErrorCode.PARAMS_ERROR, "OSS objectKey cannot be blank");
        ThrowUtils.throwIf(file == null || !file.exists() || !file.isFile(), ErrorCode.PARAMS_ERROR, "File does not exist");
        validateOssConfig();

        String normalizedObjectKey = normalizeObjectKey(objectKey);
        String endpoint = normalizeEndpoint(ossProperties.getEndpoint());
        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(file.length());
        metadata.setContentType(StrUtil.blankToDefault(FileUtil.getMimeType(file.getName()), "application/octet-stream"));

        try (InputStream inputStream = FileUtil.getInputStream(file)) {
            OSS ossClient = new OSSClientBuilder().build(
                    endpoint,
                    ossProperties.getAccessKeyId(),
                    ossProperties.getAccessKeySecret()
            );
            try {
                PutObjectRequest putObjectRequest = new PutObjectRequest(
                        ossProperties.getBucketName(),
                        normalizedObjectKey,
                        inputStream,
                        metadata
                );
                ossClient.putObject(putObjectRequest);
            } finally {
                ossClient.shutdown();
            }
            return buildFileUrl(normalizedObjectKey);
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "File upload to OSS failed");
        }
    }

    private String uploadImageFile(MultipartFile imageFile, String objectKey, String bizName) {
        String endpoint = normalizeEndpoint(ossProperties.getEndpoint());

        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(imageFile.getSize());
        metadata.setContentType(StrUtil.blankToDefault(imageFile.getContentType(), "application/octet-stream"));

        try (InputStream inputStream = imageFile.getInputStream()) {
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
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, bizName + "文件读取失败");
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, bizName + "上传失败");
        }
    }

    private void validateImageFile(MultipartFile imageFile, long maxSize, String bizName) {
        ThrowUtils.throwIf(imageFile == null || imageFile.isEmpty(), ErrorCode.PARAMS_ERROR, bizName + "文件不能为空");
        ThrowUtils.throwIf(imageFile.getSize() > maxSize, ErrorCode.PARAMS_ERROR, bizName + "文件大小不能超过 5MB");

        String contentType = imageFile.getContentType();
        ThrowUtils.throwIf(StrUtil.isBlank(contentType) || !contentType.startsWith("image/"),
                ErrorCode.PARAMS_ERROR, "仅支持上传图片文件");

        String extension = FileUtil.extName(imageFile.getOriginalFilename());
        ThrowUtils.throwIf(StrUtil.isBlank(extension) || !ALLOWED_IMAGE_EXTENSIONS.contains(extension.toLowerCase()),
                ErrorCode.PARAMS_ERROR, bizName + "文件格式不支持");
    }

    private void validateOssConfig() {
        ThrowUtils.throwIf(StrUtil.hasBlank(
                ossProperties.getBucketName(),
                ossProperties.getAccessKeyId(),
                ossProperties.getAccessKeySecret(),
                ossProperties.getEndpoint()
        ), ErrorCode.SYSTEM_ERROR, "OSS 配置不完整");
    }

    private String buildImageObjectKey(String dir, String originalFilename) {
        String extension = FileUtil.extName(originalFilename);
        if (StrUtil.isBlank(extension)) {
            extension = "png";
        }
        String normalizedDir = StrUtil.removeSuffix(dir, "/");
        String datePath = DateUtil.today().replace("-", "/");
        return StrUtil.format("{}/{}/{}.{}", normalizedDir, datePath, IdUtil.fastSimpleUUID(), extension.toLowerCase());
    }

    private String buildFileUrl(String objectKey) {
        String website = ossProperties.getWebSite();
        if (StrUtil.isNotBlank(website)) {
            return StrUtil.addSuffixIfNot(website, "/") + objectKey;
        }
        String endpoint = normalizeEndpoint(ossProperties.getEndpoint());
        return StrUtil.format("{}/{}/{}", endpoint, ossProperties.getBucketName(), objectKey);
    }

    private String normalizeObjectKey(String objectKey) {
        return StrUtil.removePrefix(objectKey, "/");
    }

    private String normalizeEndpoint(String endpoint) {
        if (StrUtil.startWithAnyIgnoreCase(endpoint, "http://", "https://")) {
            return endpoint;
        }
        return "https://" + endpoint;
    }
}
