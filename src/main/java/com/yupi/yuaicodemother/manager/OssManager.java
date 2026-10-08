package com.yupi.yuaicodemother.manager;

import cn.hutool.core.date.DateUtil;
import cn.hutool.core.io.FileUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import com.aliyun.oss.OSS;
import com.aliyun.oss.OSSClientBuilder;
import com.aliyun.oss.HttpMethod;
import com.aliyun.oss.model.CannedAccessControlList;
import com.aliyun.oss.model.ObjectMetadata;
import com.aliyun.oss.model.PutObjectRequest;
import com.yupi.yuaicodemother.config.OssProperties;
import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import com.yupi.yuaicodemother.exception.ThrowUtils;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Component;
import org.springframework.web.multipart.MultipartFile;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.ByteArrayInputStream;
import java.net.URL;
import java.time.Instant;
import java.util.Date;
import java.util.Set;
import java.util.UUID;
import java.util.function.Supplier;

/**
 * OSS 文件上传管理器。
 */
@Component
public class OssManager {

    private static final Set<String> ALLOWED_IMAGE_EXTENSIONS = Set.of("jpg", "jpeg", "png", "gif", "webp", "bmp");

    private final OssProperties ossProperties;
    private final Supplier<OSS> ossClientFactory;

    @Autowired
    public OssManager(OssProperties ossProperties) {
        this(ossProperties, () -> new OSSClientBuilder().build(
                normalizeEndpoint(ossProperties.getEndpoint()),
                ossProperties.getAccessKeyId(),
                ossProperties.getAccessKeySecret()));
    }

    public OssManager(OssProperties ossProperties, Supplier<OSS> ossClientFactory) {
        this.ossProperties = ossProperties;
        this.ossClientFactory = ossClientFactory;
    }

    public record KnowledgeObject(String objectKey, String displayName, String fileType, long size, String sha256) { }

    /** 上传私有知识文档，不返回永久公开地址。 */
    public KnowledgeObject uploadKnowledgeDocument(MultipartFile file) {
        var document = new KnowledgeDocumentFilePolicy(ossProperties.getMaxKnowledgeDocumentSize()).validate(file);
        validateOssConfig();
        String objectKey = knowledgePrefix() + UUID.randomUUID().toString().replace("-", "")
                + "." + document.fileType();
        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(document.size());
        metadata.setObjectAcl(CannedAccessControlList.Private);
        metadata.setContentType(switch (document.fileType()) {
            case "pdf" -> "application/pdf";
            case "docx" -> "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
            case "md" -> "text/markdown";
            case "txt" -> "text/plain";
            default -> throw new IllegalStateException("Unknown validated document type");
        });
        try (InputStream input = new ByteArrayInputStream(document.bytes())) {
            OSS client = ossClientFactory.get();
            try {
                client.putObject(new PutObjectRequest(ossProperties.getBucketName(), objectKey, input, metadata));
            } finally {
                client.shutdown();
            }
            return new KnowledgeObject(objectKey, document.displayName(), document.fileType(),
                    document.size(), document.sha256());
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "知识文档上传失败");
        }
    }

    public URL generateKnowledgeDownloadUrl(String objectKey) {
        validateKnowledgeObjectKey(objectKey);
        validateOssConfig();
        long ttl = ossProperties.getKnowledgeSignedUrlTtlSeconds();
        ThrowUtils.throwIf(ttl <= 0 || ttl > 3600, ErrorCode.SYSTEM_ERROR, "知识文档签名有效期配置无效");
        try {
            OSS client = ossClientFactory.get();
            try {
                return client.generatePresignedUrl(ossProperties.getBucketName(), objectKey,
                        Date.from(Instant.now().plusSeconds(ttl)), HttpMethod.GET);
            } finally {
                client.shutdown();
            }
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "知识文档下载链接生成失败");
        }
    }

    public void deleteKnowledgeObject(String objectKey) {
        validateKnowledgeObjectKey(objectKey);
        validateOssConfig();
        try {
            OSS client = ossClientFactory.get();
            try {
                client.deleteObject(ossProperties.getBucketName(), objectKey);
            } finally {
                client.shutdown();
            }
        } catch (Exception e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "知识文档删除失败");
        }
    }

    private void validateKnowledgeObjectKey(String objectKey) {
        String prefix = knowledgePrefix();
        ThrowUtils.throwIf(objectKey == null || !objectKey.startsWith(prefix)
                        || !objectKey.substring(prefix.length()).matches("[0-9a-f]{32}\\.(pdf|docx|md|txt)"),
                ErrorCode.PARAMS_ERROR, "知识文档对象键无效");
    }

    private String knowledgePrefix() {
        String dir = ossProperties.getKnowledgeDir();
        ThrowUtils.throwIf(dir == null || !dir.matches("[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*"),
                ErrorCode.SYSTEM_ERROR, "知识文档目录配置无效");
        return dir + "/";
    }

    /**
     * 上传用户头像并返回公开地址。
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
     * 上传社区帖子图片并返回公开地址。
     */
    public String uploadCommunityImage(MultipartFile imageFile) {
        validateImageFile(imageFile, ossProperties.getMaxAvatarSize(), "社区图片");
        validateOssConfig();
        String objectKey = buildImageObjectKey("community-image", imageFile.getOriginalFilename());
        return uploadImageFile(imageFile, objectKey, "社区图片");
    }

    /**
     * 上传本地文件并返回公开地址。
     */
    public String uploadFile(String objectKey, File file) {
        ThrowUtils.throwIf(StrUtil.isBlank(objectKey), ErrorCode.PARAMS_ERROR, "OSS objectKey cannot be blank");
        ThrowUtils.throwIf(file == null || !file.exists() || !file.isFile(), ErrorCode.PARAMS_ERROR, "File does not exist");
        validateOssConfig();

        String normalizedObjectKey = normalizeObjectKey(objectKey);
        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(file.length());
        metadata.setContentType(StrUtil.blankToDefault(FileUtil.getMimeType(file.getName()), "application/octet-stream"));

        try (InputStream inputStream = FileUtil.getInputStream(file)) {
            OSS ossClient = ossClientFactory.get();
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
        ObjectMetadata metadata = new ObjectMetadata();
        metadata.setContentLength(imageFile.getSize());
        metadata.setContentType(StrUtil.blankToDefault(imageFile.getContentType(), "application/octet-stream"));

        try (InputStream inputStream = imageFile.getInputStream()) {
            OSS ossClient = ossClientFactory.get();
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

    private static String normalizeEndpoint(String endpoint) {
        if (StrUtil.startWithAnyIgnoreCase(endpoint, "http://", "https://")) {
            return endpoint;
        }
        return "https://" + endpoint;
    }
}
