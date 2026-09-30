package com.yupi.yuaicodemother.customerservice;

import com.yupi.yuaicodemother.exception.BusinessException;
import com.yupi.yuaicodemother.manager.KnowledgeDocumentFilePolicy;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockMultipartFile;

import java.io.ByteArrayOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipOutputStream;

import static org.junit.jupiter.api.Assertions.*;

class KnowledgeDocumentFilePolicyTest {
    private final KnowledgeDocumentFilePolicy policy = new KnowledgeDocumentFilePolicy(20L * 1024 * 1024);

    @Test
    void acceptsFourFormatsAndComputesSha256() throws Exception {
        byte[] pdf = "%PDF-1.7\n1 0 obj\n<<>>\nendobj\n".getBytes(StandardCharsets.US_ASCII);
        byte[] docx = docxBytes();
        byte[] md = "# 产品说明\n正文".getBytes(StandardCharsets.UTF_8);
        byte[] txt = "纯文本说明".getBytes(StandardCharsets.UTF_8);
        assertAccepted("guide.pdf", "application/pdf", pdf, "pdf");
        assertAccepted("guide.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", docx, "docx");
        assertAccepted("guide.md", "text/markdown", md, "md");
        assertAccepted("guide.txt", "text/plain", txt, "txt");
    }

    @Test
    void acceptsUnencryptedPdfWhosePageTextMentionsEncrypt() throws Exception {
        byte[] pdf = ordinaryPdfWithText("The /Encrypt PDF trailer entry is optional.");
        assertAccepted("pdf-guide.pdf", "application/pdf", pdf, "pdf");
    }

    @Test
    void rejectsEmptyUnknownExtensionAndMismatchedMime() {
        assertInvalid("empty.txt", "text/plain", new byte[0]);
        assertInvalid("run.exe", "text/plain", "hello".getBytes(StandardCharsets.UTF_8));
        assertInvalid("guide.pdf", "text/plain", "%PDF-1.7".getBytes(StandardCharsets.US_ASCII));
        assertInvalid("guide.txt", "application/pdf", "hello".getBytes(StandardCharsets.UTF_8));
    }

    @Test
    void rejectsMismatchedSignaturesAndInvalidText() throws Exception {
        assertInvalid("fake.pdf", "application/pdf", "MZ".getBytes(StandardCharsets.US_ASCII));
        assertInvalid("fake.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "PKnot-a-zip".getBytes(StandardCharsets.US_ASCII));
        assertInvalid("fake.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                zipBytes(false, true, false));
        assertInvalid("binary.md", "text/markdown", new byte[]{'x', 0, 'y'});
        assertInvalid("invalid.txt", "text/plain", new byte[]{(byte) 0xc3, (byte) 0x28});
    }

    @Test
    void rejectsOverLimitAndBoundedReadsEvenWhenMultipartSizeLies() {
        byte[] tooLarge = new byte[20 * 1024 * 1024 + 1];
        tooLarge[0] = 'a';
        assertInvalid("large.txt", "text/plain", tooLarge);
        MockMultipartFile dishonest = new MockMultipartFile("file", "large.txt", "text/plain", tooLarge) {
            @Override public long getSize() { return 1; }
        };
        assertThrows(BusinessException.class, () -> policy.validate(dishonest));
    }

    @Test
    void normalizesDisplayNameAndRejectsBlankOrOversizedName() {
        byte[] bytes = "hello".getBytes(StandardCharsets.UTF_8);
        var accepted = policy.validate(file("../folder\\manual\u0001.txt", "text/plain", bytes));
        assertEquals("manual.txt", accepted.displayName());
        assertInvalid(".txt", "text/plain", bytes);
        assertInvalid("x".repeat(129) + ".txt", "text/plain", bytes);
    }

    @Test
    void rejectsDocxZipBombAndMacroEntry() throws Exception {
        assertInvalid("many.docx", docxMime(), zipBytes(true, true, false, 1025));
        assertInvalid("macro.docx", docxMime(), zipBytes(true, true, true));
        byte[] expanded = new byte[33 * 1024 * 1024];
        assertInvalid("huge.docx", docxMime(), zipBytes(true, true, false, expanded));
        assertInvalid("total.docx", docxMime(), zipWithTotalExpandedOverLimit());
    }

    @Test
    void rejectsMacroContentTypesEvenWhenPartHasAnInnocentName() throws Exception {
        String macroMain = "<Types><Override PartName=\"/word/document.xml\" "
                + "ContentType=\"application/vnd.ms-word.document.macroEnabled.main+xml\"/></Types>";
        String vbaPart = "<Types><Override PartName=\"/word/payload.bin\" "
                + "ContentType=\"application/vnd.ms-office.vbaProject\"/></Types>";
        String defaultVba = "<Types><Default Extension=\"bin\" "
                + "ContentType=\"application/vnd.ms-office.vbaProject\"/></Types>";
        assertInvalid("macro.docx", docxMime(), zipWithXml(macroMain, null, null, null));
        assertInvalid("macro.docx", docxMime(), zipWithXml(vbaPart, null, null, null));
        assertInvalid("macro.docx", docxMime(), zipWithXml(defaultVba, null, null, null));
    }

    @Test
    void rejectsVbaRelationshipRegardlessOfRelationshipFileLocation() throws Exception {
        String vbaRelationship = "<Relationships><Relationship Id=\"rId1\" "
                + "Type=\"http://schemas.microsoft.com/office/2006/relationships/vbaProject\" "
                + "Target=\"payload.bin\"/></Relationships>";
        assertInvalid("root.docx", docxMime(), zipWithXml("<Types/>", "_rels/.rels", vbaRelationship, null));
        assertInvalid("document.docx", docxMime(), zipWithXml("<Types/>",
                "word/_rels/document.xml.rels", vbaRelationship, null));
        assertInvalid("nested.docx", docxMime(), zipWithXml("<Types/>",
                "custom/_rels/custom.xml.rels", vbaRelationship, null));
    }

    @Test
    void rejectsXmlExternalEntitiesAndOversizedMetadata() throws Exception {
        String xxe = "<!DOCTYPE Types [<!ENTITY secret SYSTEM \"file:///C:/private-secret\">]>"
                + "<Types><Override ContentType=\"&secret;\"/></Types>";
        BusinessException error = assertThrows(BusinessException.class,
                () -> policy.validate(file("xxe.docx", docxMime(), zipWithXml(xxe, null, null, null))));
        assertFalse(error.getMessage().contains("private-secret"));
        assertInvalid("huge-metadata.docx", docxMime(), zipWithXml(
                "<Types><!--" + "x".repeat(1024 * 1024) + "--></Types>", null, null, null));
    }

    @Test
    void acceptsOrdinaryDocxRelationships() throws Exception {
        String contentTypes = "<Types><Override PartName=\"/word/document.xml\" "
                + "ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/></Types>";
        String relationships = "<Relationships><Relationship Id=\"rId1\" "
                + "Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" "
                + "Target=\"styles.xml\"/></Relationships>";
        assertAccepted("ordinary.docx", docxMime(), zipWithXml(contentTypes,
                "word/_rels/document.xml.rels", relationships, null), "docx");
    }

    private void assertAccepted(String name, String mime, byte[] bytes, String type) throws Exception {
        var result = policy.validate(file(name, mime, bytes));
        assertEquals(name, result.displayName());
        assertEquals(type, result.fileType());
        assertEquals(bytes.length, result.size());
        assertEquals(HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes)), result.sha256());
        assertArrayEquals(bytes, result.bytes());
    }

    private void assertInvalid(String name, String mime, byte[] bytes) {
        assertThrows(BusinessException.class, () -> policy.validate(file(name, mime, bytes)));
    }

    private MockMultipartFile file(String name, String mime, byte[] bytes) {
        return new MockMultipartFile("file", name, mime, bytes);
    }

    private static String docxMime() {
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document";
    }

    private static byte[] docxBytes() throws Exception {
        return zipBytes(true, true, false);
    }

    private static byte[] zipBytes(boolean contentTypes, boolean document, boolean macro) throws Exception {
        return zipBytes(contentTypes, document, macro, 0);
    }

    private static byte[] zipBytes(boolean contentTypes, boolean document, boolean macro, int extras) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(output)) {
            if (contentTypes) addEntry(zip, "[Content_Types].xml", "<Types/>".getBytes(StandardCharsets.UTF_8));
            if (document) addEntry(zip, "word/document.xml", "<document/>".getBytes(StandardCharsets.UTF_8));
            if (macro) addEntry(zip, "word/vbaProject.bin", new byte[]{1});
            for (int i = 0; i < extras; i++) addEntry(zip, "extra-" + i, new byte[]{1});
        }
        return output.toByteArray();
    }

    private static byte[] zipBytes(boolean contentTypes, boolean document, boolean macro, byte[] expanded) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(output)) {
            if (contentTypes) addEntry(zip, "[Content_Types].xml", "<Types/>".getBytes(StandardCharsets.UTF_8));
            if (document) addEntry(zip, "word/document.xml", expanded);
            if (macro) addEntry(zip, "word/vbaProject.bin", new byte[]{1});
        }
        return output.toByteArray();
    }

    private static void addEntry(ZipOutputStream zip, String name, byte[] bytes) throws Exception {
        zip.putNextEntry(new ZipEntry(name));
        zip.write(bytes);
        zip.closeEntry();
    }

    private static byte[] zipWithTotalExpandedOverLimit() throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        byte[] block = new byte[1024 * 1024];
        try (ZipOutputStream zip = new ZipOutputStream(output)) {
            addEntry(zip, "[Content_Types].xml", "<Types/>".getBytes(StandardCharsets.UTF_8));
            for (int entry = 0; entry < 3; entry++) {
                zip.putNextEntry(new ZipEntry(entry == 0 ? "word/document.xml" : "word/extra-" + entry));
                for (int mb = 0; mb < 23; mb++) zip.write(block);
                zip.closeEntry();
            }
        }
        return output.toByteArray();
    }

    private static byte[] zipWithXml(String contentTypes, String relationshipName,
                                     String relationships, byte[] payload) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        try (ZipOutputStream zip = new ZipOutputStream(output)) {
            addEntry(zip, "[Content_Types].xml", contentTypes.getBytes(StandardCharsets.UTF_8));
            addEntry(zip, "word/document.xml", "<document/>".getBytes(StandardCharsets.UTF_8));
            if (relationshipName != null) {
                addEntry(zip, relationshipName, relationships.getBytes(StandardCharsets.UTF_8));
            }
            if (payload != null) addEntry(zip, "word/payload.bin", payload);
        }
        return output.toByteArray();
    }

    private static byte[] ordinaryPdfWithText(String text) throws Exception {
        ByteArrayOutputStream output = new ByteArrayOutputStream();
        int[] offsets = new int[6];
        writeAscii(output, "%PDF-1.4\n");
        String content = "BT /F1 12 Tf 72 720 Td (" + text + ") Tj ET\n";
        String[] objects = {
                "<< /Type /Catalog /Pages 2 0 R >>",
                "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                        + "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
                "<< /Length " + content.getBytes(StandardCharsets.US_ASCII).length + " >>\nstream\n"
                        + content + "endstream",
                "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
        };
        for (int index = 1; index <= objects.length; index++) {
            offsets[index] = output.size();
            writeAscii(output, index + " 0 obj\n" + objects[index - 1] + "\nendobj\n");
        }
        int xrefOffset = output.size();
        writeAscii(output, "xref\n0 6\n0000000000 65535 f \n");
        for (int index = 1; index <= objects.length; index++) {
            writeAscii(output, String.format(Locale.ROOT, "%010d 00000 n \n", offsets[index]));
        }
        writeAscii(output, "trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
                + xrefOffset + "\n%%EOF\n");
        return output.toByteArray();
    }

    private static void writeAscii(ByteArrayOutputStream output, String text) throws Exception {
        output.write(text.getBytes(StandardCharsets.US_ASCII));
    }
}
