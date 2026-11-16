package com.yupi.yuaicodemother.customerservice;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;

import java.nio.file.Files;
import java.nio.file.Path;
import java.sql.Connection;
import java.sql.SQLException;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

/** 真实 MySQL DDL 仅限本次随机 schema；业务库永不执行迁移。 */
@EnabledIfEnvironmentVariable(named = "AI_KNOWLEDGE_MYSQL_MIGRATION_INTEGRATION", matches = "true")
class KnowledgeRevisionMysqlMigrationTest {
    @Test
    void backupRoundTripPreservesEscapesNullDatesAndGeneratedColumns() throws Exception {
        isolated(connection -> {
            seed(connection, 1, "ACTIVE", 1, 1, 2);
            String escaped = "中文'\\\\;\n--保留原文";
            try (var statement = connection.prepareStatement("UPDATE customer_service_knowledge_document SET name=?,objectKey=?,lastErrorCode=NULL,createTime='2020-01-02 03:04:05' WHERE id=1")) {
                statement.setString(1, escaped);
                statement.setString(2, escaped);
                statement.executeUpdate();
            }
            Path directory = Path.of("tmp");
            Files.createDirectories(directory);
            Path backup = Files.createTempFile(directory, "isolated-knowledge-backup-", ".sql");
            try {
                try (var writer = Files.newBufferedWriter(backup)) {
                    assertEquals(1, new KnowledgeRevisionMysqlApplyTest().backupTable(connection, "customer_service_knowledge_document", writer));
                }
                try (var statement = connection.createStatement()) {
                    statement.execute("DROP TABLE customer_service_knowledge_document");
                }
                KnowledgeRevisionMysqlSupport.script(connection, Files.readString(backup));
                try (var statement = connection.createStatement(); var result = statement.executeQuery("SELECT name,objectKey,lastErrorCode,createTime,activeContentHash,contentHash FROM customer_service_knowledge_document WHERE id=1")) {
                    assertTrue(result.next());
                    assertEquals(escaped, result.getString(1));
                    assertEquals(escaped, result.getString(2));
                    assertNull(result.getString(3));
                    assertEquals("2020-01-02 03:04:05", result.getString(4));
                    assertEquals(result.getString(6), result.getString(5));
                }
            } finally { Files.deleteIfExists(backup); }
        });
    }

    @Test
    void migratesActiveIdentityAndRegistersEveryCurrentContent() throws Exception {
        isolated(connection -> {
            seed(connection, 1, "ACTIVE", 2, 2, 7);
            seed(connection, 2, "DISABLED", 3, 1, 9);
            migrate(connection);
            try (var statement = connection.createStatement(); var result = statement.executeQuery(
                    "SELECT activationRevision,indexedEtlVersion,indexedActivationRevision FROM customer_service_knowledge_document WHERE id=1")) {
                assertTrue(result.next());
                assertEquals(1, result.getLong(1));
                assertEquals(7, result.getLong(2));
                assertEquals(1, result.getLong(3));
            }
            assertEquals(2, scalar(connection, "SELECT COUNT(*) FROM customer_service_knowledge_content_revision"));
            assertEquals(0, scalar(connection, "SELECT indexedEtlVersion FROM customer_service_knowledge_document WHERE id=2"));
            assertEquals(0, scalar(connection, "SELECT indexedActivationRevision FROM customer_service_knowledge_document WHERE id=2"));
            assertEquals(0, scalar(connection, "SELECT COUNT(*) FROM customer_service_knowledge_content_revision c JOIN customer_service_knowledge_document d ON c.documentId=d.id WHERE c.documentVersion<>d.documentVersion OR c.objectKey<>d.objectKey OR c.contentHash<>d.contentHash"));
        });
    }

    @Test
    void rejectsBothPendingAndProcessingBeforeSchemaChanges() throws Exception {
        for (String status : new String[]{"PENDING", "PROCESSING"}) {
            isolated(connection -> {
                seed(connection, 1, "ACTIVE", 1, 1, 2);
                try (var statement = connection.createStatement()) {
                    statement.executeUpdate("INSERT INTO customer_service_knowledge_etl_outbox(id,documentId,documentVersion,etlVersion,operation,status) VALUES(1,1,1,2,'INDEX','" + status + "')");
                }
                SQLException failure = assertThrows(SQLException.class, () -> migrate(connection));
                assertEquals("45000", failure.getSQLState());
                assertTrue(failure.getMessage().contains("Drain legacy"));
                assertNotMigrated(connection);
            });
        }
    }

    @Test
    void rejectsUnavailableHistoricalServingBeforeSchemaChanges() throws Exception {
        isolated(connection -> {
            seed(connection, 1, "FAILED", 2, 1, 4);
            SQLException failure = assertThrows(SQLException.class, () -> migrate(connection));
            assertEquals("45000", failure.getSQLState());
            assertTrue(failure.getMessage().contains("Legacy serving revision unavailable"));
            assertNotMigrated(connection);
        });
    }

    private void assertNotMigrated(Connection connection) throws Exception {
        assertEquals(0, scalar(connection, "SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customer_service_knowledge_document' AND COLUMN_NAME='activationRevision'"));
        assertEquals(0, scalar(connection, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='customer_service_knowledge_content_revision'"));
    }

    private void seed(Connection connection, long id, String status, long content, long indexed, long etl) throws Exception {
        try (var statement = connection.prepareStatement("INSERT INTO customer_service_knowledge_document(id,name,fileType,objectKey,fileSize,contentHash,documentVersion,indexedVersion,etlVersion,status,createdBy,updatedBy) VALUES(?, '隔离测试', 'txt', 'isolated/fake.txt', 1, ?, ?, ?, ?, ?, 1, 1)")) {
            statement.setLong(1, id);
            statement.setString(2, String.format("%064d", id));
            statement.setLong(3, content);
            statement.setLong(4, indexed);
            statement.setLong(5, etl);
            statement.setString(6, status);
            statement.executeUpdate();
        }
    }

    private void migrate(Connection connection) throws Exception {
        KnowledgeRevisionMysqlSupport.script(connection, Files.readString(Path.of("sql", "alter_customer_service_knowledge_v2_revisions.sql")));
    }

    private long scalar(Connection connection, String sql) throws Exception {
        try (var statement = connection.createStatement(); var result = statement.executeQuery(sql)) {
            assertTrue(result.next()); return result.getLong(1);
        }
    }

    private void isolated(Scenario scenario) throws Exception {
        String schema = "knowledge_v2_it_" + UUID.randomUUID().toString().replace("-", "");
        validateSchema(schema);
        try (var connection = KnowledgeRevisionMysqlSupport.connect()) {
            boolean created = false;
            try {
                try (var statement = connection.createStatement()) {
                    statement.execute("CREATE DATABASE `" + schema + "` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci");
                    created = true;
                }
                connection.setCatalog(schema);
                assertEquals(schema, connection.getCatalog());
                // 从当前建表脚本剔除 v2 部分还原旧结构，之后实际执行仓库迁移而非测试替代品。
                String baseline = Files.readString(Path.of("sql", "alter_customer_service_knowledge.sql"));
                baseline = baseline.substring(0, baseline.indexOf("-- 内容版本原文登记"));
                baseline = baseline.replaceAll("(?m)^\\s*(activationRevision|indexedEtlVersion|indexedActivationRevision) BIGINT.*\\R", "");
                KnowledgeRevisionMysqlSupport.script(connection, baseline);
                scenario.run(connection);
            } finally {
                if (created) {
                    // 严格限定当前生成的完整名称，绝不枚举或批量清理历史 schema。
                    validateSchema(schema);
                    connection.setCatalog("yu_ai_code_mother");
                    try (var statement = connection.createStatement()) { statement.execute("DROP DATABASE `" + schema + "`"); }
                }
            }
        }
    }

    private static void validateSchema(String schema) {
        if (!schema.matches("knowledge_v2_it_[0-9a-f]{32}")) throw new IllegalArgumentException("隔离 schema 名称不合法");
    }

    @FunctionalInterface
    private interface Scenario { void run(Connection connection) throws Exception; }
}
