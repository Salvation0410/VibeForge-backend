package com.yupi.yuaicodemother.customerservice;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeDocumentMapper;
import com.yupi.yuaicodemother.mapper.CustomerServiceKnowledgeEtlOutboxMapper;
import org.apache.ibatis.annotations.Update;

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
        assertTrue(Pattern.compile("(?i)`?lastErrorCode`?\\s+VARCHAR\\(128\\)").matcher(table).find(),
                "document error codes must preserve the Python 128-character contract");
        assertFalse(table.toUpperCase().contains("AUTO_INCREMENT"), "Snowflake IDs must be assigned by Java");
        assertTrue(table.contains("uk_knowledge_document_active_hash"),
                "active document hashes need a database uniqueness backstop");
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
        assertTrue(Pattern.compile("(?i)`?lastErrorCode`?\\s+VARCHAR\\(128\\)").matcher(table).find(),
                "outbox error codes must preserve the Python 128-character contract");
    }

    @Test
    void mutationCoordinatorUsesGlobalGuardAndAuditableLeaseTable() throws IOException {
        String sql = Files.readString(MIGRATION);
        String guard = tableDefinition("customer_service_knowledge_mutation_guard");
        String lease = tableDefinition("customer_service_knowledge_mutation_lease");
        assertColumn(guard, "nextFence");
        for (String column : new String[]{"operationId", "scope", "operation", "fence", "expiresAt", "revokedAt"}) {
            assertColumn(lease, column);
        }
        assertFalse(lease.toLowerCase().contains("proof"), "HMAC proofs must never be stored");
        assertTrue(sql.contains("INSERT IGNORE INTO customer_service_knowledge_mutation_guard"));
    }

    @Test
    void task7UpgradeMigrationHandlesExistingTaskOneSchema() throws Exception {
        Path upgrade = Path.of("sql", "alter_customer_service_knowledge_task7.sql");
        String sql = Files.readString(upgrade);
        assertTrue(sql.contains("information_schema.COLUMNS"));
        assertTrue(sql.contains("information_schema.STATISTICS"));
        assertTrue(sql.contains("uk_knowledge_document_active_hash"));
        assertTrue(sql.contains("customer_service_knowledge_mutation_guard"));
        assertTrue(sql.contains("customer_service_knowledge_mutation_lease"));
        assertTrue(sql.toLowerCase().contains("duplicate"));
        assertTrue(Pattern.compile("(?i)ALTER TABLE customer_service_knowledge_document MODIFY COLUMN lastErrorCode VARCHAR\\(128\\)")
                .matcher(sql).find());
        assertTrue(Pattern.compile("(?i)ALTER TABLE customer_service_knowledge_etl_outbox MODIFY COLUMN lastErrorCode VARCHAR\\(128\\)")
                .matcher(sql).find());
    }

    @Test
    void indexingTransitionExplicitlyAllowsIdempotentReentry() throws Exception {
        Update update = CustomerServiceKnowledgeDocumentMapper.class
                .getMethod("markIndexing", long.class, long.class, long.class).getAnnotation(Update.class);
        assertTrue(update.value()[0].contains("'INDEXING'"));
    }

    @Test
    void claimRefreshRequiresCurrentProcessingOwner() throws Exception {
        Update update = CustomerServiceKnowledgeEtlOutboxMapper.class
                .getMethod("refreshClaim", long.class, String.class, java.time.LocalDateTime.class)
                .getAnnotation(Update.class);
        String sql = update.value()[0];
        assertTrue(sql.contains("status='PROCESSING'"));
        assertTrue(sql.contains("processingOwner=#{owner}"));
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
