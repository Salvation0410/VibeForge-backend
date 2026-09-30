package com.yupi.yuaicodemother.customerservice;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

class CustomerServiceKnowledgeSchemaTest {

    private static final Path MIGRATION = Path.of("sql", "alter_customer_service_knowledge.sql");

    @Test
    void documentTableDefinesVersionedContent() throws IOException {
        String table = tableDefinition("customer_service_knowledge_document");

        for (String column : new String[]{"documentVersion", "contentHash", "indexedVersion",
                "etlVersion", "isDelete"}) {
            assertColumn(table, column);
        }
        assertTrue(Pattern.compile("(?i)`?contentHash`?\\s+CHAR\\(64\\)").matcher(table).find(),
                "contentHash must store a SHA-256 hex digest");
        assertFalse(table.toUpperCase().contains("AUTO_INCREMENT"), "Snowflake IDs must be assigned by Java");
    }

    @Test
    void outboxTableDefinesClaimAndDeduplicationKeys() throws IOException {
        String table = tableDefinition("customer_service_knowledge_etl_outbox");

        for (String column : new String[]{"documentId", "documentVersion", "etlVersion",
                "processingDeadline"}) {
            assertColumn(table, column);
        }
        assertTrue(Pattern.compile("(?is)UNIQUE\\s+KEY\\s+`?uk_document_operation_version`?\\s*"
                + "\\(`?documentId`?,\\s*`?operation`?,\\s*`?documentVersion`?,\\s*`?etlVersion`?\\)")
                .matcher(table).find());
        assertTrue(Pattern.compile("(?i)KEY\\s+`?idx_etl_claim`?\\s*\\(")
                .matcher(table).find());
        assertFalse(table.toUpperCase().contains("FOREIGN KEY"));
        assertFalse(Pattern.compile("(?m)^\\s*`?isDelete`?\\s+").matcher(table).find(),
                "Outbox jobs must remain visible");
    }

    private static String tableDefinition(String name) throws IOException {
        String sql = Files.readString(MIGRATION);
        Matcher matcher = Pattern.compile("(?is)CREATE\\s+TABLE\\s+IF\\s+NOT\\s+EXISTS\\s+`?"
                + name + "`?\\s*\\((.*?)\\)\\s*ENGINE").matcher(sql);
        assertTrue(matcher.find(), "Missing table: " + name);
        return matcher.group(1);
    }

    private static void assertColumn(String table, String column) {
        assertTrue(Pattern.compile("(?m)^\\s*`?" + column + "`?\\s+\\w+").matcher(table).find(),
                "Missing column: " + column);
    }
}
