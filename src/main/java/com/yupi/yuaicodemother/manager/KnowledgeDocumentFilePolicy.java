package com.yupi.yuaicodemother.manager;

import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.exception.ErrorCode;
import org.springframework.web.multipart.MultipartFile;
import org.w3c.dom.Document;
import org.w3c.dom.Element;
import org.w3c.dom.NodeList;
import org.xml.sax.SAXException;
import org.xml.sax.SAXParseException;
import org.xml.sax.helpers.DefaultHandler;

import javax.xml.XMLConstants;
import javax.xml.parsers.DocumentBuilderFactory;
import javax.xml.parsers.ParserConfigurationException;
import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.HexFormat;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

/** 知识文档进入 OSS 边界前的格式、大小和安全校验策略。 */
public final class KnowledgeDocumentFilePolicy {
    private static final long HARD_MAX_BYTES = 20L * 1024 * 1024;
    private static final int MAX_NAME_LENGTH = 128;
    private static final int MAX_ZIP_ENTRIES = 1024;
    private static final long MAX_ZIP_ENTRY_BYTES = 32L * 1024 * 1024;
    private static final long MAX_ZIP_EXPANDED_BYTES = 64L * 1024 * 1024;
    private static final int MAX_XML_PART_BYTES = 1024 * 1024;
    private static final Map<String, Set<String>> MIME_TYPES = Map.of(
            "pdf", Set.of("application/pdf"),
            "docx", Set.of("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            "md", Set.of("text/markdown", "text/x-markdown", "text/plain"),
            "txt", Set.of("text/plain")
    );

    private final long maxBytes;

    public KnowledgeDocumentFilePolicy(long configuredMaxBytes) {
        if (configuredMaxBytes <= 0) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "知识文档大小配置无效");
        }
        this.maxBytes = Math.min(configuredMaxBytes, HARD_MAX_BYTES);
    }

    public ValidatedDocument validate(MultipartFile file) {
        if (file == null || file.isEmpty()) {
            throw invalid("文件不能为空");
        }
        if (file.getSize() > maxBytes) {
            throw invalid("文件大小不能超过限制");
        }
        String displayName = normalizeName(file.getOriginalFilename());
        int dot = displayName.lastIndexOf('.');
        if (dot <= 0 || dot == displayName.length() - 1) {
            throw invalid("文件格式不支持");
        }
        String type = displayName.substring(dot + 1).toLowerCase(Locale.ROOT);
        Set<String> allowedMimes = MIME_TYPES.get(type);
        if (allowedMimes == null) {
            throw invalid("文件格式不支持");
        }
        String mime = file.getContentType();
        if (mime == null || !allowedMimes.contains(mime.split(";", 2)[0].trim().toLowerCase(Locale.ROOT))) {
            throw invalid("文件内容与类型不匹配");
        }
        byte[] bytes;
        try (InputStream input = file.getInputStream()) {
            bytes = input.readNBytes((int) maxBytes + 1);
        } catch (IOException e) {
            throw new BusinessException(ErrorCode.SYSTEM_ERROR, "知识文档读取失败");
        }
        if (bytes.length == 0 || bytes.length > maxBytes) {
            throw invalid(bytes.length == 0 ? "文件不能为空" : "文件大小不能超过限制");
        }
        switch (type) {
            case "pdf" -> validatePdf(bytes);
            case "docx" -> validateDocx(bytes);
            case "md", "txt" -> validateText(bytes);
            default -> throw invalid("文件格式不支持");
        }
        return new ValidatedDocument(bytes, displayName, type, bytes.length, sha256(bytes));
    }

    private static String normalizeName(String original) {
        if (original == null) {
            throw invalid("文件名不能为空");
        }
        String name = original.replace('\\', '/');
        name = name.substring(name.lastIndexOf('/') + 1);
        name = name.replaceAll("[\\p{Cntrl}]", "").trim();
        if (name.isBlank() || name.length() > MAX_NAME_LENGTH || name.startsWith(".")) {
            throw invalid("文件名无效");
        }
        return name;
    }

    private static void validatePdf(byte[] bytes) {
        if (bytes.length < 5 || bytes[0] != '%' || bytes[1] != 'P' || bytes[2] != 'D'
                || bytes[3] != 'F' || bytes[4] != '-') {
            throw invalid("文件内容与类型不匹配");
        }
        // Python 的 pypdf 会在 ETL 阶段检查加密；页面正文可以合法包含 "/Encrypt" 文本。
    }

    private static void validateDocx(byte[] bytes) {
        if (bytes.length < 4 || bytes[0] != 'P' || bytes[1] != 'K' || bytes[2] != 3 || bytes[3] != 4) {
            throw invalid("文件内容与类型不匹配");
        }
        boolean contentTypes = false;
        boolean document = false;
        int entries = 0;
        long totalExpanded = 0;
        try (ZipInputStream zip = new ZipInputStream(new ByteArrayInputStream(bytes))) {
            ZipEntry entry;
            byte[] buffer = new byte[8192];
            while ((entry = zip.getNextEntry()) != null) {
                if (++entries > MAX_ZIP_ENTRIES) {
                    throw invalid("DOCX 压缩条目超限");
                }
                String name = entry.getName();
                boolean contentTypePart = name.equalsIgnoreCase("[Content_Types].xml");
                boolean relationshipPart = name.toLowerCase(Locale.ROOT).endsWith(".rels");
                if (contentTypePart) contentTypes = true;
                if (name.equals("word/document.xml")) document = true;
                if (name.toLowerCase(Locale.ROOT).endsWith("vbaproject.bin")) {
                    throw invalid("不支持含宏文档");
                }
                long entryExpanded = 0;
                ByteArrayOutputStream xmlBytes = contentTypePart || relationshipPart ? new ByteArrayOutputStream() : null;
                int count;
                while ((count = zip.read(buffer)) != -1) {
                    entryExpanded += count;
                    totalExpanded += count;
                    if (entryExpanded > MAX_ZIP_ENTRY_BYTES || totalExpanded > MAX_ZIP_EXPANDED_BYTES) {
                        throw invalid("DOCX 解压大小超限");
                    }
                    if (xmlBytes != null) {
                        if (entryExpanded > MAX_XML_PART_BYTES) {
                            throw invalid("DOCX 元数据超限");
                        }
                        xmlBytes.write(buffer, 0, count);
                    }
                }
                if (xmlBytes != null && containsMacroDeclaration(xmlBytes.toByteArray(), contentTypePart)) {
                    throw invalid("不支持含宏文档");
                }
                zip.closeEntry();
            }
        } catch (IOException e) {
            throw invalid("文件内容与类型不匹配");
        }
        if (!contentTypes || !document) {
            throw invalid("文件内容与类型不匹配");
        }
    }

    private static boolean containsMacroDeclaration(byte[] xml, boolean contentTypesPart) {
        Document document = parseSafeXml(xml);
        NodeList elements = document.getElementsByTagName("*");
        String attribute = contentTypesPart ? "ContentType" : "Type";
        for (int index = 0; index < elements.getLength(); index++) {
            String value = ((Element) elements.item(index)).getAttribute(attribute).toLowerCase(Locale.ROOT);
            if (value.contains("macroenabled") || value.contains("vbaproject")) {
                return true;
            }
        }
        return false;
    }

    private static Document parseSafeXml(byte[] xml) {
        try {
            DocumentBuilderFactory factory = DocumentBuilderFactory.newInstance();
            factory.setFeature(XMLConstants.FEATURE_SECURE_PROCESSING, true);
            factory.setFeature("http://apache.org/xml/features/disallow-doctype-decl", true);
            factory.setFeature("http://xml.org/sax/features/external-general-entities", false);
            factory.setFeature("http://xml.org/sax/features/external-parameter-entities", false);
            factory.setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false);
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_DTD, "");
            factory.setAttribute(XMLConstants.ACCESS_EXTERNAL_SCHEMA, "");
            factory.setXIncludeAware(false);
            factory.setExpandEntityReferences(false);
            var builder = factory.newDocumentBuilder();
            builder.setErrorHandler(new DefaultHandler() {
                @Override
                public void error(SAXParseException exception) throws SAXException {
                    throw exception;
                }

                @Override
                public void fatalError(SAXParseException exception) throws SAXException {
                    throw exception;
                }
            });
            return builder.parse(new ByteArrayInputStream(xml));
        } catch (ParserConfigurationException | SAXException | IOException | IllegalArgumentException e) {
            throw invalid("DOCX 元数据无效");
        }
    }

    private static void validateText(byte[] bytes) {
        try {
            String text = StandardCharsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString();
            for (int index = 0; index < text.length(); index++) {
                char ch = text.charAt(index);
                if (Character.isISOControl(ch) && ch != '\n' && ch != '\r' && ch != '\t') {
                    throw invalid("文件内容与类型不匹配");
                }
            }
        } catch (CharacterCodingException e) {
            throw invalid("文件内容与类型不匹配");
        }
    }

    private static String sha256(byte[] bytes) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 unavailable", e);
        }
    }

    private static BusinessException invalid(String message) {
        return new BusinessException(ErrorCode.PARAMS_ERROR, message);
    }

    public record ValidatedDocument(byte[] bytes, String displayName, String fileType, long size, String sha256) { }
}
